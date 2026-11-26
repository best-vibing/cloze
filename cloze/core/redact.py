"""빈칸 자리를 주변 배경색으로 칠해 지운다. 결정은 ADR-003(래스터 채우기)이다."""

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageStat

from cloze.core.extract.render import PixelBox, pixel_box
from cloze.core.gate import PAD_RATIO as PAD_RATIO
from cloze.core.gate import blank_bbox as blank_bbox
from cloze.core.models import BBox

# [가설] 배경색을 잴 박스 바깥 띠의 두께(px). long_side_px=2000 기준이다.
BAND_PX = 3
# [가설] WebP 품질(pdf-extraction.md 3절).
WEBP_QUALITY = 85
# 띠에서도, 모든 박스를 뺀 페이지 전체에서도 샘플을 얻지 못할 때(박스가 이미지를 덮을 때) 쓰는 색.
FALLBACK_COLOR = (255, 255, 255)

type RGB = tuple[int, int, int]


@dataclass(frozen=True)
class RedactResult:
    image: Image.Image
    colors: list[str]  # 칸마다 칠한 색(#rrggbb), boxes와 같은 순서


def redact(image: Image.Image, boxes: list[BBox]) -> RedactResult:
    """입력 이미지는 바꾸지 않는다. 색은 모두 원본에서 먼저 잰 뒤 복사본에 칠한다."""
    pixels = [pixel_box(box, image.size) for box in boxes]
    colors = [_background(image, box, pixels) for box in pixels]
    canvas = image.copy()
    for box, color in zip(pixels, colors, strict=True):
        canvas.paste(color, box)
    return RedactResult(image=canvas, colors=[_hex(color) for color in colors])


def encode_webp(image: Image.Image, quality: int = WEBP_QUALITY) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=quality)
    return buffer.getvalue()


def _background(image: Image.Image, box: PixelBox, all_boxes: list[PixelBox]) -> RGB:
    """박스 바깥 띠의 채널별 중앙값. 띠에서 샘플을 얻지 못하면 모든 박스를 뺀 페이지 전체의
    중앙값을, 그것도 없으면 FALLBACK_COLOR를 쓴다."""
    band = _median_outside(image, _band_region(box, image.size), all_boxes)
    if band is not None:
        return band
    page = _median_outside(image, (0, 0, *image.size), all_boxes)
    return page if page is not None else FALLBACK_COLOR


def _median_outside(image: Image.Image, region: PixelBox, boxes: list[PixelBox]) -> RGB | None:
    """region 안에서 모든 박스(자기 박스 포함)를 뺀 픽셀의 채널별 중앙값. 없으면 None."""
    mask = Image.new("L", (region[2] - region[0], region[3] - region[1]), 255)
    draw = ImageDraw.Draw(mask)
    left, top = region[0], region[1]
    for x0, y0, x1, y1 in boxes:
        # rectangle은 끝 좌표를 포함하므로 1을 뺀다.
        draw.rectangle((x0 - left, y0 - top, x1 - left - 1, y1 - top - 1), fill=0)
    stat = ImageStat.Stat(image.crop(region), mask)
    if stat.count[0] == 0:
        return None
    red, green, blue = (int(value) for value in stat.median[:3])
    return (red, green, blue)


def _band_region(box: PixelBox, size: tuple[int, int]) -> PixelBox:
    """박스를 띠 두께만큼 넓힌 영역. 이미지 밖으로 나간 부분은 자른다."""
    width, height = size
    return (
        max(box[0] - BAND_PX, 0),
        max(box[1] - BAND_PX, 0),
        min(box[2] + BAND_PX, width),
        min(box[3] + BAND_PX, height),
    )


def _hex(color: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)
