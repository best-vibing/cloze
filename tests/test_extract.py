import json
from pathlib import Path

import pytest

from cloze.core.extract import extract_tokens, word_box
from cloze.core.models import PageTokens

SAMPLE = Path(__file__).parent / "fixtures" / "sample.pdf"


@pytest.fixture(scope="module")
def pages() -> list[PageTokens]:
    return extract_tokens(SAMPLE.read_bytes())


def test_page_info(pages: list[PageTokens]) -> None:
    assert [page.page.page_no for page in pages] == [1, 2, 3]
    assert pages[0].page.width_pt == 612.0
    assert pages[0].page.height_pt == 792.0
    assert pages[0].page.source == "textlayer"


def test_ids_follow_reading_order(pages: list[PageTokens]) -> None:
    tokens = pages[0].tokens
    assert [token.id for token in tokens] == list(range(len(tokens)))
    assert [token.text for token in tokens[:5]] == [
        "Cloze",
        "Sample",
        "Handbook",
        "Gradient",
        "Descent",
    ]


def test_bbox_is_normalized_from_top_left(pages: list[PageTokens]) -> None:
    for token in pages[0].tokens:
        assert all(0.0 <= value <= 1.0 for value in token.bbox)
        assert token.bbox[0] < token.bbox[2]
        assert token.bbox[1] < token.bbox[3]
    by_text = {token.text: token for token in pages[0].tokens}
    assert by_text["Handbook"].bbox[1] < by_text["Gradient"].bbox[1] < by_text["Page"].bbox[1]


def test_line_ids_group_nearby_tops(pages: list[PageTokens]) -> None:
    by_text = {token.text: token for token in pages[0].tokens}
    assert by_text["Gradient"].line_id == by_text["Descent"].line_id
    assert by_text["Momentum"].line_id == by_text["smooths"].line_id
    assert by_text["Gradient"].line_id != by_text["optimizer"].line_id


def test_font_attributes(pages: list[PageTokens]) -> None:
    by_text = {token.text: token for token in pages[0].tokens}
    assert by_text["Gradient"].font_size == 24.0
    assert by_text["Gradient"].fontname == "Helvetica-Bold"
    assert by_text["optimizer"].fontname == "Helvetica"


def test_korean_text_is_extracted(pages: list[PageTokens]) -> None:
    assert "역전파는" in [token.text for token in pages[2].tokens]


def test_upright_extraction_matches_select_cli_baseline(pages: list[PageTokens]) -> None:
    # sample.tokens.json은 feat/select-cli(3e78ddf) 시점 코드로 뽑은 결과다.
    # 좌표 기준·회전 처리를 바꿔도 직립 페이지의 결과가 그대로인지 확인한다.
    # word_bbox는 그 뒤에 더한 필드라 기준선에 없다. 값은 아래 테스트가 따로 확인한다.
    baseline = json.loads((SAMPLE.parent / "sample.tokens.json").read_text(encoding="utf-8"))
    without = {"tokens": {"__all__": {"word_bbox"}}}
    assert [page.model_dump(mode="json", exclude=without) for page in pages] == baseline


def test_word_bbox_excludes_trailing_punctuation(pages: list[PageTokens]) -> None:
    by_text = {token.text: token for token in pages[0].tokens}
    plain, dotted = by_text["gradients"], by_text["class."]
    assert plain.word_bbox is None  # 뗄 부호가 없으면 bbox와 같다는 뜻으로 None
    assert dotted.word_bbox is not None
    assert dotted.word_bbox[0] == dotted.bbox[0]
    assert dotted.word_bbox[1:] == pytest.approx(
        (dotted.bbox[1], dotted.bbox[2] - 3.33 / 612, dotted.bbox[3]), abs=1e-4
    )


def _char(text: str, x0: float, x1: float) -> dict[str, object]:
    return {"text": text, "x0": x0, "x1": x1, "top": 10.0, "bottom": 20.0}


def test_word_box_strips_both_edges_and_keeps_inner_punctuation() -> None:
    chars = [
        _char("(", 0, 4),
        _char("e", 4, 10),
        _char(".", 10, 12),
        _char("g", 12, 18),
        _char(")", 18, 22),
        _char(",", 22, 25),
    ]
    assert word_box(chars) == (4.0, 10.0, 18.0, 20.0)
    assert word_box([_char("(", 0, 4), _char(")", 4, 8)]) is None  # 부호뿐이면 그대로 둔다
    assert word_box([_char("a", 0, 4), _char("b", 4, 8)]) is None  # 뗄 부호가 없다
