from pathlib import Path

import pytest
from PIL import Image

from cloze.core.extract import extract_tokens
from cloze.core.extract.render import DEBUG_COLOR, draw_boxes, pixel_box, render_page
from cloze.core.models import BBox, PageTokens

RENDER_PDF = (Path(__file__).parent / "fixtures" / "render.pdf").read_bytes()
WHITE, YELLOW, ROTATED, CROPPED, ROTATED_CROP, UPSIDE_DOWN, ROTATED_LEFT = range(1, 8)
ALL_PAGES = [WHITE, YELLOW, ROTATED, CROPPED, ROTATED_CROP, UPSIDE_DOWN, ROTATED_LEFT]
TITLES = {
    WHITE: "White Page",
    YELLOW: "Yellow Page",
    ROTATED: "Rotated Page",
    CROPPED: "Cropped Page",
    ROTATED_CROP: "Rotated Crop",
    UPSIDE_DOWN: "Upside Down",
    ROTATED_LEFT: "Rotated Left",
}
DARK = 100  # 회색조 값이 이보다 작으면 글자 픽셀로 본다.


@pytest.fixture(scope="module")
def pages() -> list[PageTokens]:
    return extract_tokens(RENDER_PDF)


def dark_ratio(image: Image.Image, box: BBox) -> float:
    histogram = image.crop(pixel_box(box, image.size)).convert("L").histogram()
    return sum(histogram[:DARK]) / max(sum(histogram), 1)


def _word_box(page: PageTokens, text: str) -> BBox:
    return next(token.bbox for token in page.tokens if token.text == text)


@pytest.mark.parametrize("page_no", ALL_PAGES)
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


@pytest.mark.parametrize("page_no", ALL_PAGES)
def test_words_read_in_order_on_every_rotation(pages: list[PageTokens], page_no: int) -> None:
    # 회전된 페이지도 단어가 갈라지거나 거꾸로 읽히지 않고, 줄·글자 크기가 직립 페이지와 같다.
    tokens = pages[page_no - 1].tokens
    assert [token.text for token in tokens] == [*TITLES[page_no].split(), "Target", "Corner"]
    assert [token.line_id for token in tokens] == [0, 0, 1, 2]
    assert [token.font_size for token in tokens] == [24.0, 24.0, 18.0, 18.0]
    assert [token.fontname for token in tokens][:2] == ["Helvetica-Bold"] * 2


@pytest.mark.parametrize("page_no", ALL_PAGES)
@pytest.mark.parametrize("word", ["Target", "Corner"])
def test_word_boxes_cover_rendered_glyphs(pages: list[PageTokens], page_no: int, word: str) -> None:
    image = render_page(RENDER_PDF, page_no)
    assert dark_ratio(image, _word_box(pages[page_no - 1], word)) > 0.1


@pytest.mark.parametrize("page_no", [WHITE, YELLOW, CROPPED, UPSIDE_DOWN])
def test_shifted_box_misses_glyphs(pages: list[PageTokens], page_no: int) -> None:
    image = render_page(RENDER_PDF, page_no)
    box = _word_box(pages[page_no - 1], "Target")
    assert dark_ratio(image, (box[0], box[1] + 0.1, box[2], box[3] + 0.1)) == 0.0


def test_cropped_page_uses_visible_area(pages: list[PageTokens]) -> None:
    # pypdfium2는 CropBox만 그리므로 좌표·크기도 CropBox 기준이어야 한다.
    for page_no, size in [(CROPPED, (512, 642)), (ROTATED_CROP, (642, 512))]:
        info = pages[page_no - 1].page
        assert (info.width_pt, info.height_pt) == size


@pytest.mark.parametrize("page_no", [CROPPED, ROTATED_CROP])
def test_text_outside_cropbox_is_not_a_token(pages: list[PageTokens], page_no: int) -> None:
    assert "Hidden" not in [token.text for token in pages[page_no - 1].tokens]


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
