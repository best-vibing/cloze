from pathlib import Path

import pytest
from PIL import Image

from cloze.core.extract import extract_tokens
from cloze.core.extract.render import DEBUG_COLOR, draw_boxes, pixel_box, render_page
from cloze.core.models import BBox, PageTokens

RENDER_PDF = (Path(__file__).parent / "fixtures" / "render.pdf").read_bytes()
WHITE, YELLOW, ROTATED, CROPPED = 1, 2, 3, 4
DARK = 100  # 회색조 값이 이보다 작으면 글자 픽셀로 본다.


@pytest.fixture(scope="module")
def pages() -> list[PageTokens]:
    return extract_tokens(RENDER_PDF)


def dark_ratio(image: Image.Image, box: BBox) -> float:
    histogram = image.crop(pixel_box(box, image.size)).convert("L").histogram()
    return sum(histogram[:DARK]) / max(sum(histogram), 1)


def _word_box(page: PageTokens, text: str) -> BBox:
    return next(token.bbox for token in page.tokens if token.text == text)


@pytest.mark.parametrize("page_no", [WHITE, YELLOW, ROTATED])
def test_image_matches_page_size(pages: list[PageTokens], page_no: int) -> None:
    info = pages[page_no - 1].page
    image = render_page(RENDER_PDF, page_no)
    assert image.mode == "RGB"
    assert max(image.size) == 2000
    assert abs(image.width - 2000 * info.width_pt / max(info.width_pt, info.height_pt)) <= 1
    assert abs(image.height - 2000 * info.height_pt / max(info.width_pt, info.height_pt)) <= 1


def test_rotated_page_is_landscape(pages: list[PageTokens]) -> None:
    assert (pages[ROTATED - 1].page.width_pt, pages[ROTATED - 1].page.height_pt) == (792, 612)
    width, height = render_page(RENDER_PDF, ROTATED).size
    assert width == 2000
    assert abs(height - 2000 * 612 / 792) <= 1


def test_long_side_is_configurable() -> None:
    assert max(render_page(RENDER_PDF, WHITE, long_side_px=500).size) == 500


@pytest.mark.parametrize("page_no", [WHITE, YELLOW])
@pytest.mark.parametrize("word", ["Target", "Corner"])
def test_word_boxes_cover_rendered_glyphs(pages: list[PageTokens], page_no: int, word: str) -> None:
    image = render_page(RENDER_PDF, page_no)
    box = _word_box(pages[page_no - 1], word)
    assert dark_ratio(image, box) > 0.1
    shifted = (box[0], box[1] + 0.1, box[2], box[3] + 0.1)
    assert dark_ratio(image, shifted) == 0.0


def test_rotated_page_boxes_cover_rendered_glyphs(pages: list[PageTokens]) -> None:
    # 회전 페이지는 토큰이 한 글자씩 갈라지므로(아래 xfail) 글자 토큰마다 확인한다.
    image = render_page(RENDER_PDF, ROTATED)
    tokens = pages[ROTATED - 1].tokens
    assert "".join(token.text for token in tokens).count("T") >= 1
    assert len(tokens) >= len("RotatedPageTargetCorner") // 2
    assert all(dark_ratio(image, token.bbox) > 0.05 for token in tokens)


@pytest.mark.xfail(
    strict=True,
    reason="회전 페이지에서 pdfplumber의 글자 size가 글자마다 달라 단어가 한 글자씩 갈라진다",
)
def test_rotated_page_keeps_words(pages: list[PageTokens]) -> None:
    assert "Target" in {token.text for token in pages[ROTATED - 1].tokens}


@pytest.mark.xfail(
    strict=True,
    reason="pdfplumber는 MediaBox, pypdfium2는 CropBox 기준이라 어긋난다. 보정 여부는 결정 필요",
)
def test_cropped_page_boxes_cover_rendered_glyphs(pages: list[PageTokens]) -> None:
    image = render_page(RENDER_PDF, CROPPED)
    assert dark_ratio(image, _word_box(pages[CROPPED - 1], "Target")) > 0.1


def test_pixel_box_rounds_outward() -> None:
    assert pixel_box((0.101, 0.2, 0.299, 0.5), (100, 10)) == (10, 2, 30, 5)
    assert pixel_box((0.0, 0.0, 1.0, 1.0), (7, 3)) == (0, 0, 7, 3)


def test_draw_boxes_outlines_copy_in_red() -> None:
    image = Image.new("RGB", (100, 100), "white")
    drawn = draw_boxes(image, [(0.1, 0.1, 0.5, 0.5)])
    assert image.getpixel((10, 10)) == (255, 255, 255)
    assert drawn.getpixel((10, 10)) == DEBUG_COLOR
    assert drawn.getpixel((49, 49)) == DEBUG_COLOR
    assert drawn.getpixel((30, 30)) == (255, 255, 255)
    assert drawn.getpixel((50, 50)) == (255, 255, 255)
