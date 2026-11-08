import io
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from cloze.core.extract import extract_tokens
from cloze.core.extract.render import pixel_box, render_page
from cloze.core.models import BBox, PageInfo, PageTokens, Token
from cloze.core.redact import FALLBACK_COLOR, blank_bbox, encode_webp, redact

RENDER_PDF = (Path(__file__).parent / "fixtures" / "render.pdf").read_bytes()
YELLOW_RGB = (255, 255, 204)  # make_fixtures.py의 연노랑 배경
DARK = 100


def _page(boxes: list[BBox], width: float = 600, height: float = 800) -> PageTokens:
    tokens = [
        Token(id=i, text="w", bbox=box, line_id=0, font_size=12, fontname="R")
        for i, box in enumerate(boxes)
    ]
    return PageTokens(page=PageInfo(page_no=1, width_pt=width, height_pt=height), tokens=tokens)


def _restored(result: Image.Image, original: Image.Image, boxes: list[BBox]) -> Image.Image:
    """칠한 자리만 원본으로 되돌린다. 원본과 같으면 박스 바깥은 바뀌지 않은 것이다."""
    copy = result.copy()
    for box in boxes:
        area = pixel_box(box, original.size)
        copy.paste(original.crop(area), area)
    return copy


def _dark_count(image: Image.Image) -> int:
    return sum(image.convert("L").histogram()[:DARK])


def test_blank_bbox_unions_tokens_and_pads_evenly() -> None:
    page = _page([(0.1, 0.5, 0.2, 0.6), (0.25, 0.5, 0.4, 0.6), (0.7, 0.1, 0.8, 0.2)])
    x0, y0, x1, y1 = blank_bbox(page, [0, 1], pad_ratio=0.1)
    pad_pt = 0.1 * 800 * 0.1
    assert (x0, x1) == pytest.approx((0.1 - pad_pt / 600, 0.4 + pad_pt / 600))
    assert (y0, y1) == pytest.approx((0.5 - pad_pt / 800, 0.6 + pad_pt / 800))


def test_blank_bbox_is_clipped_to_page() -> None:
    page = _page([(0.0, 0.0, 0.1, 0.05), (0.95, 0.9, 1.0, 1.0)])
    assert blank_bbox(page, [0])[:2] == (0.0, 0.0)
    assert blank_bbox(page, [1])[2:] == (1.0, 1.0)


def test_redact_fills_box_and_keeps_rest() -> None:
    original = Image.new("RGB", (100, 100), YELLOW_RGB)
    original.paste((0, 0, 0), (22, 42, 58, 48))  # 글자 대신 검은 막대
    before = original.tobytes()
    box = (0.2, 0.4, 0.6, 0.5)
    result = redact(original, [box])
    assert result.colors == ["#ffffcc"]
    assert result.image.crop(pixel_box(box, original.size)).getcolors() == [(40 * 10, YELLOW_RGB)]
    assert (
        ImageChops.difference(_restored(result.image, original, [box]), original).getbbox() is None
    )
    assert original.tobytes() == before


def test_no_boxes_returns_unchanged_copy() -> None:
    original = Image.new("RGB", (10, 10), "white")
    result = redact(original, [])
    assert result.colors == []
    assert result.image is not original
    assert result.image.tobytes() == original.tobytes()


def test_boxes_touching_image_edges() -> None:
    original = Image.new("RGB", (50, 40), (10, 200, 30))
    boxes: list[BBox] = [(0.0, 0.0, 0.2, 0.25), (0.8, 0.75, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0)]
    result = redact(original, boxes[:2])
    assert result.colors == ["#0ac81e", "#0ac81e"]
    assert redact(original, boxes[2:]).colors == ["#{:02x}{:02x}{:02x}".format(*FALLBACK_COLOR)]


def test_neighbor_boxes_are_not_sampled_as_background() -> None:
    # 위아래 줄에 붙은 칸(파란색)이 띠의 대부분을 차지해도 흰 배경을 골라야 한다.
    original = Image.new("RGB", (100, 100), "white")
    above, middle, below = (0.2, 0.36, 0.6, 0.4), (0.2, 0.4, 0.6, 0.44), (0.2, 0.44, 0.6, 0.48)
    for box in (above, below):
        original.paste((0, 0, 255), pixel_box(box, original.size))
    assert redact(original, [above, middle, below]).colors[1] == "#ffffff"
    assert redact(original, [middle]).colors[0] != "#ffffff"


@pytest.mark.parametrize(("page_no", "expected"), [(1, (255, 255, 255)), (2, YELLOW_RGB)])
def test_fixture_word_is_filled_with_page_background(
    page_no: int, expected: tuple[int, ...]
) -> None:
    page = extract_tokens(RENDER_PDF)[page_no - 1]
    image = render_page(RENDER_PDF, page_no)
    target = next(token.id for token in page.tokens if token.text == "Target")
    box = blank_bbox(page, [target])
    result = redact(image, [box])
    color = tuple(int(result.colors[0][i : i + 2], 16) for i in (1, 3, 5))
    assert all(abs(got - want) <= 3 for got, want in zip(color, expected, strict=True))
    assert _dark_count(image.crop(pixel_box(box, image.size))) > 0
    assert len(result.image.crop(pixel_box(box, image.size)).getcolors() or []) == 1


def test_webp_does_not_leak_answer_glyphs() -> None:
    page = extract_tokens(RENDER_PDF)[1]
    image = render_page(RENDER_PDF, 2)
    boxes = [blank_bbox(page, [token.id]) for token in page.tokens]
    data = encode_webp(redact(image, boxes).image)
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    decoded = Image.open(io.BytesIO(data)).convert("RGB")
    assert decoded.size == image.size
    for box in boxes:
        x0, y0, x1, y1 = pixel_box(box, image.size)
        # 손실 압축은 경계에서 번질 수 있어 가장자리 2px을 뺀 안쪽을 본다.
        assert _dark_count(decoded.crop((x0 + 2, y0 + 2, x1 - 2, y1 - 2))) == 0
