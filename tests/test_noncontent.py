import json
from pathlib import Path

import pytest

from cloze.cli.__main__ import main
from cloze.core.extract.render import render_page
from cloze.core.filter import filter_candidates
from cloze.core.models import PageInfo, PageTokens, Policy, Token
from cloze.core.redact import encode_webp

FIXTURES = Path(__file__).parent / "fixtures"
POLICY = Policy(seed=0)


def _page(*lines: tuple[float, float, str], page_no: int = 1) -> PageTokens:
    return PageTokens(
        page=PageInfo(page_no=page_no, width_pt=600, height_pt=800),
        tokens=[
            Token(
                id=line_id * 100 + index,
                text=word,
                bbox=(0.36 + index * 0.035, top, 0.39 + index * 0.035, top + size / 800),
                line_id=line_id,
                font_size=size,
                fontname="Helvetica",
            )
            for line_id, (top, size, text) in enumerate(lines)
            for index, word in enumerate(text.split())
        ],
    )


@pytest.mark.parametrize(
    "title",
    ["End", "The End", "Thank You", "Thanks", "감사합니다", "수고하셨습니다", "Q & A"],
)
def test_closing_requires_final_position(title: str) -> None:
    page = _page((0.42, 36, title))
    following = _page((0.1, 24, "Gradient Descent"), page_no=2)
    assert {item.reason for item in filter_candidates([page], POLICY)[0].excluded} == {"noncontent"}
    assert "noncontent" not in {
        item.reason for item in filter_candidates([page, following], POLICY)[0].excluded
    }


@pytest.mark.parametrize(
    "lines",
    [
        [(0.3, 38, "Cover")],
        [(0.2, 35, "목차"), (0.29, 18, "1. 신경망"), (0.33, 18, "2. Regularization")],
        [(0.2, 35, "Homework"), (0.29, 18, "Due: Friday"), (0.33, 18, "Submit via: LMS")],
        [(0.3, 35, "과제 안내"), (0.38, 18, "제출 기한: 금요일")],
    ],
)
def test_nonlearning_layouts(lines: list[tuple[float, float, str]]) -> None:
    page = _page(*lines)
    original = page.model_copy(deep=True)
    first = filter_candidates([page], POLICY)
    assert first == filter_candidates([page], POLICY)
    assert page == original
    assert not first[0].candidate_ids
    assert {item.reason for item in first[0].excluded} == {"noncontent"}


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Control Flow", "The end of a loop updates counters."),
        ("END", "The keyword terminates an SQL block."),
        ("Contents", "A table of contents maps headings to sections."),
        ("Cover", "The algorithm keeps the smallest covering interval."),
        ("Thank You", "감사는 Social feedback의 구성 요소다."),
        ("Q&A", "A Q&A corpus trains the retrieval model."),
        ("Assignment", "Explain why gradient descent converges."),
        ("Cover", "Momentum accelerates convergence"),
        ("Neural Networks", "Momentum accelerates convergence"),
    ],
)
def test_heading_with_learning_content_is_preserved(title: str, body: str) -> None:
    page = _page((0.28, 38, title), (0.49, 18, body))
    result = filter_candidates([page], POLICY)[0]
    assert "noncontent" not in {item.reason for item in result.excluded}
    assert any(id_ >= 100 for id_ in result.candidate_ids)


def test_first_concept_slide_with_two_short_lines_is_preserved() -> None:
    cover_like = _page(
        (0.30, 40, "Gradient Descent"), (0.45, 24, "Learning rate"), (0.52, 24, "Momentum")
    )
    next_page = _page((0.10, 30, "Next slide"), page_no=2)
    candidates = filter_candidates([cover_like, next_page], POLICY)[0]
    assert 200 in candidates.candidate_ids


def test_code_ocr_and_incomplete_text_are_preserved() -> None:
    code = _page((0.42, 36, "END"))
    code.tokens[0].fontname = "Courier"
    ocr = _page((0.42, 36, "End"))
    ocr.page.source = "ocr"
    incomplete = _page((0.42, 36, "End"), (0.5, 15, "\ufffd"))
    for page in (code, ocr, incomplete):
        assert filter_candidates([page], POLICY)[0].candidate_ids == [0]


def test_ambiguous_lower_block_keeps_cover_and_learning_tokens() -> None:
    page = _page((0.19, 34, "Cover"), (0.62, 18, "Momentum accelerates convergence."))
    result = filter_candidates([page], POLICY)[0]
    assert 0 in result.candidate_ids
    assert 100 in result.candidate_ids


@pytest.mark.parametrize(
    "fixture, unmasked, learning",
    [
        ("noncontent.pdf", (1, 2, 4, 5), 3),
        ("twoup.pdf", (), 1),
    ],
)
def test_mask_preserves_noncontent_pixels_and_masks_learning_content(
    tmp_path: Path, fixture: str, unmasked: tuple[int, ...], learning: int
) -> None:
    pdf = FIXTURES / fixture
    original = pdf.read_bytes()
    main(["mask", str(pdf), "--out", str(tmp_path), "--density", "100"])
    assert pdf.read_bytes() == original
    for number in unmasked:
        stem = tmp_path / f"page_{number:03d}"
        assert json.loads(stem.with_suffix(".json").read_text("utf-8"))["blanks"] == []
        assert stem.with_suffix(".webp").read_bytes() == encode_webp(render_page(original, number))
    stem = tmp_path / f"page_{learning:03d}"
    blanks = json.loads(stem.with_suffix(".json").read_text("utf-8"))["blanks"]
    assert blanks
    if fixture == "twoup.pdf":
        assert all(blank["bbox"][1] > 0.5 for blank in blanks)
    assert stem.with_suffix(".webp").read_bytes() != encode_webp(render_page(original, learning))
