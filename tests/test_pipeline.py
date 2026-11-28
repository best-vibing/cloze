import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from cloze.core import pipeline
from cloze.core.errors import (
    LLMInvalidResponse,
    LLMPermanentError,
    LLMQuotaExhausted,
    LLMTransientError,
)
from cloze.core.models import PageInfo, PageTokens, Policy, Token


def page_with_text(number: int, text: str) -> PageTokens:
    return PageTokens(
        page=PageInfo(page_no=number, width_pt=100, height_pt=100),
        tokens=[
            Token(
                id=0,
                text=text,
                bbox=(0.1, 0.1, 0.2, 0.2),
                line_id=0,
                font_size=12,
                fontname="Arial",
            )
        ],
    )


def test_prepare_preserves_low_text_pages_and_images(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [page_with_text(1, "인공지능은 데이터를 학습한다"), page_with_text(2, "2")]
    monkeypatch.setattr(pipeline, "extract_tokens", Mock(return_value=pages))
    images = [Image.new("RGB", (2, 2)), Image.new("RGB", (3, 3))]
    render = Mock(side_effect=images)
    monkeypatch.setattr(pipeline, "render_page", render)

    prepared = pipeline.prepare(b"test pdf")

    assert [page.has_text for page in prepared] == [True, False]
    assert [page.tokens for page in prepared] == pages
    assert [page.image for page in prepared] == images
    assert [call.args[1] for call in render.call_args_list] == [1, 2]


def test_prepare_rejects_exactly_eighty_percent_low_text(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [page_with_text(1, "Readable body")] + [page_with_text(i, str(i)) for i in range(2, 6)]
    monkeypatch.setattr(pipeline, "extract_tokens", Mock(return_value=pages))
    render = Mock()
    monkeypatch.setattr(pipeline, "render_page", render)

    with pytest.raises(pipeline.UnreadablePDFError, match="unreadable_pdf"):
        pipeline.prepare(b"test pdf")

    render.assert_not_called()


def test_prepare_reads_short_korean_fixture() -> None:
    pdf = (Path(__file__).parent / "fixtures" / "korean.pdf").read_bytes()
    assert sum(page.has_text for page in pipeline.prepare(pdf)) >= 4


@pytest.mark.parametrize(
    ("name", "expected_word"), [("sample.pdf", "Gradient"), ("korean.pdf", "인공지능은")]
)
def test_analyze_prepared_pdf_without_live_api(name: str, expected_word: str) -> None:
    pdf = (Path(__file__).parent / "fixtures" / name).read_bytes()
    pages = pipeline.prepare(pdf)
    response = {"doc_type": "강의 슬라이드", "preset": "concept", "reason": "테스트 응답"}
    ask = AsyncMock(return_value=response)

    assert asyncio.run(pipeline.analyze_document(pages, ask)) == response | {"source": "llm"}
    ask.assert_awaited_once()
    call = ask.await_args
    assert call is not None
    assert expected_word in call.args[0]


def test_prepare_rejects_document_with_only_repeated_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [page_with_text(i, f"Lecture notes {i}") for i in range(1, 6)]
    for page in pages:
        page.tokens[0].bbox = (0.1, 0.94, 0.3, 0.97)
    monkeypatch.setattr(pipeline, "extract_tokens", Mock(return_value=pages))
    render = Mock()
    monkeypatch.setattr(pipeline, "render_page", render)
    with pytest.raises(pipeline.UnreadablePDFError):
        pipeline.prepare(b"test pdf")
    render.assert_not_called()


def prepared_page(
    number: int, words: tuple[str, ...] = ("physics", "energy"), *, has_text: bool = True
) -> pipeline.PreparedPage:
    tokens = [
        Token(
            id=i,
            text=word,
            bbox=(0.1, 0.1 + 0.15 * i, 0.3, 0.18 + 0.15 * i),
            line_id=i,
            font_size=12,
            fontname="Arial",
        )
        for i, word in enumerate(words)
    ]
    page = PageTokens(page=PageInfo(page_no=number, width_pt=100, height_pt=100), tokens=tokens)
    return pipeline.PreparedPage(page, Image.new("RGB", (100, 100), "white"), has_text)


@pytest.mark.parametrize(
    ("outcomes", "expected", "attempts"),
    [
        ([{"blanks": [{"token_ids": [0], "type": "term"}]}], "gemini", 1),
        (
            [LLMInvalidResponse("bad schema"), {"blanks": [{"token_ids": [0], "type": "term"}]}],
            "gemini",
            2,
        ),
        ([LLMInvalidResponse("one"), LLMInvalidResponse("two")], "rule", 2),
        ([LLMPermanentError("unavailable")], "rule", 1),
    ],
)
def test_build_selection_paths(outcomes: list[object], expected: str, attempts: int) -> None:
    ask = AsyncMock(side_effect=outcomes)
    factory = Mock(return_value=ask)

    async def run() -> list[pipeline.PageResult]:
        return [
            result
            async for result in pipeline.build(
                [prepared_page(1)],
                Policy(preset="concept", seed=2, density=50),
                factory,
                skip_pages=set(),
                analysis=None,
                avoid_ids={},
            )
        ]

    (result,) = asyncio.run(run())
    assert result.source == expected
    assert result.attempts == ask.await_count == attempts
    assert len(result.blanks) == 1
    assert result.image_webp.startswith(b"RIFF")
    if attempts == 2:
        assert "재시도" in ask.await_args_list[1].args[0]


@pytest.mark.parametrize("failure", [LLMPermanentError, LLMQuotaExhausted])
def test_build_after_permanent_error_skips_completed_and_keeps_low_text_pages(
    failure: type[LLMPermanentError],
) -> None:
    ask = AsyncMock(side_effect=failure("unavailable"))
    factory = Mock(return_value=ask)

    async def run() -> list[pipeline.PageResult]:
        return [
            result
            async for result in pipeline.build(
                [
                    prepared_page(1, ("molecular", "theory")),
                    prepared_page(2),
                    prepared_page(3, ("short", "line"), has_text=False),
                    prepared_page(4, ("velocity", "momentum")),
                    prepared_page(5, ("acceleration",) * 4),
                ],
                Policy(seed=2, density=50),
                factory,
                skip_pages={1},
                analysis=None,
                avoid_ids={},
            )
        ]

    results = asyncio.run(run())
    factory.assert_called_once_with("select", 2)
    assert [(p.page_no, p.source, p.attempts) for p in results] == [
        (2, "rule", 1),
        (3, "none", 0),
        (4, "rule", 0),
        (5, "none", 0),
    ]


def test_build_propagates_transient_error_for_worker_retry() -> None:
    ask = AsyncMock(side_effect=LLMTransientError("try later"))

    async def run() -> None:
        async for _ in pipeline.build(
            [prepared_page(1)],
            Policy(seed=1, density=50),
            Mock(return_value=ask),
            skip_pages=set(),
            analysis=None,
            avoid_ids={},
        ):
            pytest.fail("a transient error should not emit a page")

    with pytest.raises(LLMTransientError, match="try later"):
        asyncio.run(run())
    ask.assert_awaited_once()


def test_build_retries_a_selection_with_an_exposed_answer() -> None:
    ask = AsyncMock(
        side_effect=[
            {"blanks": [{"token_ids": [0], "type": "term"}]},
            {"blanks": [{"token_ids": [4], "type": "term"}]},
        ]
    )

    async def run() -> list[pipeline.PageResult]:
        return [
            result
            async for result in pipeline.build(
                [prepared_page(1, ("physics", "physics", "physics", "physics", "energy"))],
                Policy(seed=1, density=20),
                Mock(return_value=ask),
                skip_pages=set(),
                analysis=None,
                avoid_ids={},
            )
        ]

    (result,) = asyncio.run(run())
    assert result.source == "gemini"
    assert result.attempts == 2
    assert [blank.answer for blank in result.blanks] == ["energy"]
    retry_prompt = ask.await_args_list[1].args[0]
    assert "answer_exposed" in retry_prompt
    assert "token_ids" in retry_prompt
