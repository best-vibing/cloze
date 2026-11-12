import json
from pathlib import Path

import pytest

from cloze.core.extract import extract_tokens
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
    baseline = json.loads((SAMPLE.parent / "sample.tokens.json").read_text(encoding="utf-8"))
    assert [page.model_dump(mode="json") for page in pages] == baseline


KOREAN = Path(__file__).parent / "fixtures" / "korean.pdf"


def test_korean_words_split_at_gap_without_space_char() -> None:
    # 띄어쓰기가 공백 글자 없이 0.29em 간격으로만 저장된 줄.
    # pdfplumber 기본 x_tolerance(3pt)면 10pt 줄의 어절이 붙는다.
    page = extract_tokens(KOREAN.read_bytes())[0]
    lines: dict[int, list[str]] = {}
    for token in page.tokens:
        lines.setdefault(token.line_id, []).append(token.text)
    assert lines[0] == ["인공지능은", "데이터를", "학습한다"]
    assert lines[1] == ["확률", "변수의", "분포"]
    assert lines[2] == ["역전파는"]  # 어절 안 글자(간격 0)는 나뉘지 않는다
    assert lines[3] == ["gradient", "descent", "uses", "the", "learning", "rate"]


@pytest.mark.parametrize(
    "page_no", [2, 3, 4, 5], ids=["page-rotate-90", "char-90", "char-270", "char-180"]
)
def test_korean_words_split_in_every_direction(page_no: int) -> None:
    # 같은 0.29em 간격 줄을 페이지 회전·글자 회전으로 돌려도 어절이 나뉘어야 한다.
    # 세로 글(90·270도)은 pdfplumber가 y 허용치로 글자 사이 간격을 판정한다.
    page = extract_tokens(KOREAN.read_bytes())[page_no - 1]
    assert [token.text for token in page.tokens] == ["인공지능은", "데이터를", "학습한다"]
