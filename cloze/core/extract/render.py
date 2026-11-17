"""페이지를 이미지로 그린다. 좌표 규약과 렌더링 크기는 docs/design/pdf-extraction.md 2·3절이다."""

import math

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from cloze.core.models import BBox

# [가설] 렌더링한 이미지의 긴 변 픽셀 수. 바꾸면 EXTRACT_VERSION을 올린다.
LONG_SIDE_PX = 2000
DEBUG_COLOR = (255, 0, 0)
DEBUG_LINE_PX = 2

type PixelBox = tuple[int, int, int, int]


def render_page(pdf: bytes, page_no: int, long_side_px: int = LONG_SIDE_PX) -> Image.Image:
    """page_no는 1부터 센다. 페이지 회전(/Rotate)은 pypdfium2가 반영한다."""
    document = pdfium.PdfDocument(pdf)
    try:
        page = document[page_no - 1]
        width, height = page.get_size()
        bitmap = page.render(scale=long_side_px / max(width, height))
        rendered: Image.Image = bitmap.to_pil()
        # convert가 픽셀을 복사하므로 문서를 닫아도 이미지는 남는다.
        return rendered.convert("RGB")
    finally:
        document.close()


def pixel_box(box: BBox, size: tuple[int, int]) -> PixelBox:
    """0~1 bbox를 픽셀 좌표로 바꾼다. 글자가 남지 않게 바깥쪽으로 반올림한다. 끝은 미포함."""
    width, height = size
    return (
        math.floor(box[0] * width),
        math.floor(box[1] * height),
        math.ceil(box[2] * width),
        math.ceil(box[3] * height),
    )


def draw_boxes(image: Image.Image, boxes: list[BBox]) -> Image.Image:
    """좌표 확인용: 원본 복사본에 박스를 빨간 테두리로 그린다."""
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    for box in boxes:
        x0, y0, x1, y1 = pixel_box(box, canvas.size)
        corner = (max(x1 - 1, x0), max(y1 - 1, y0))
        draw.rectangle((x0, y0, *corner), outline=DEBUG_COLOR, width=DEBUG_LINE_PX)
    return canvas
