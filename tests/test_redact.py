import io
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

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
    pad_pt = 12 * 0.1  # 글자 크기 12pt 기준
    assert (x0, x1) == pytest.approx((0.1 - pad_pt / 600, 0.4 + pad_pt / 600))
    assert (y0, y1) == pytest.approx((0.5 - pad_pt / 800, 0.6 + pad_pt / 800))


def test_blank_bbox_prefers_word_bbox_over_token_bbox() -> None:
    # "class." 같은 토큰은 word_bbox가 마침표를 뺀 범위라, 칠하는 상자도 그만큼 좁아진다.
    token = Token(
        id=0,
        text="class.",
        bbox=(0.1, 0.5, 0.3, 0.6),
        word_bbox=(0.1, 0.5, 0.28, 0.6),
        line_id=0,
        font_size=12,
        fontname="R",
    )
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=[token])
    assert blank_bbox(page, [0], pad_ratio=0.0) == (0.1, 0.5, 0.28, 0.6)


def test_span_pads_only_edges_without_punctuation() -> None:
    # "(learning rate"처럼 묶인 칸은 합집합의 바깥 변으로 본다. 앞의 "("가 있는 왼쪽만 패딩이 없다.
    first = Token(
        id=0,
        text="(learning",
        bbox=(0.1, 0.5, 0.3, 0.6),
        word_bbox=(0.11, 0.5, 0.3, 0.6),
        line_id=0,
        font_size=12,
        fontname="R",
    )
    second = Token(
        id=1, text="rate", bbox=(0.32, 0.5, 0.4, 0.6), line_id=0, font_size=12, fontname="R"
    )
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=[first, second])
    pad_x, pad_y = 12 * 0.1 / 600, 12 * 0.1 / 800
    assert blank_bbox(page, [0, 1], pad_ratio=0.1) == pytest.approx(
        (0.11, 0.5 - pad_y, 0.4 + pad_x, 0.6 + pad_y)
    )


def test_vertical_word_gets_same_padding_as_horizontal() -> None:
    # 회전 페이지의 세로 단어도 bbox 길이와 상관없이 글자 크기 기준으로 같은 두께가 붙는다.
    page = _page([(0.1, 0.1, 0.4, 0.12), (0.5, 0.1, 0.52, 0.4)])
    flat, tall = blank_bbox(page, [0], pad_ratio=0.5), blank_bbox(page, [1], pad_ratio=0.5)
    assert 0.1 - flat[1] == pytest.approx(0.1 - tall[1])
    assert 0.5 - tall[0] == pytest.approx(0.1 - flat[0])


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


# [가설] WebP 손실 압축 뒤 칸 안 픽셀 중앙값과 bg_color의 채널별 허용 차이.
WEBP_COLOR_TOLERANCE = 4


def _yellow_page() -> tuple[PageTokens, Image.Image]:
    return extract_tokens(RENDER_PDF)[1], render_page(RENDER_PDF, 2)


def _rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def test_empty_band_falls_back_to_page_background() -> None:
    # 칸을 감싸는 큰 칸 때문에 띠 샘플이 없으면, 모든 박스를 뺀 페이지 전체의 중앙값(노랑)을 쓴다.
    page, image = _yellow_page()
    target = blank_bbox(page, [next(t.id for t in page.tokens if t.text == "Target")])
    margin = 0.02  # 이미지 폭 1546px 기준 약 31px로, 띠(3px)보다 넓다.
    outer = (target[0] - margin, target[1] - margin, target[2] + margin, target[3] + margin)
    colors = redact(image, [target, outer]).colors
    assert all(abs(got - want) <= 3 for got, want in zip(_rgb(colors[0]), YELLOW_RGB, strict=True))
    assert colors[0] != "#ffffff"


def test_webp_keeps_fill_color_within_tolerance() -> None:
    page, image = _yellow_page()
    boxes = [blank_bbox(page, [token.id]) for token in page.tokens]
    result = redact(image, boxes)
    decoded = Image.open(io.BytesIO(encode_webp(result.image))).convert("RGB")
    for box, color in zip(boxes, result.colors, strict=True):
        median = ImageStat.Stat(decoded.crop(pixel_box(box, decoded.size))).median
        diffs = [abs(got - want) for got, want in zip(median, _rgb(color), strict=True)]
        assert max(diffs) <= WEBP_COLOR_TOLERANCE, (color, median)


# 글자 진행 방향(도, 시계 방향)별 텍스트 행렬. make_fixtures.py의 _SIDEWAYS·_FLIPPED와 같다.
_MATRIX = {0: "1 0 0 1", 90: "0 -1 1 0", 180: "-1 0 0 -1", 270: "0 1 -1 0"}
# 부호 잉크를 셀 때 부호 띠를 글자 진행의 수직 방향으로 넓히는 폭(px). 괄호가 줄 위아래로 나온다.
_STRIP_MARGIN_PX = 6


def _one_word_pdf(text: str, angle: int) -> bytes:
    """Helvetica 12pt로 단어 하나를 페이지 가운데에 angle 방향으로 그린 한 쪽 PDF."""
    shown = text.replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /R 12 Tf {_MATRIX[angle]} 306 396 Tm ({shown}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /R 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer << /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def _punctuation_strips(token: Token, size: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """토큰 bbox 중 word_bbox 바깥(부호가 있는 띠)의 픽셀 영역들."""
    assert token.word_bbox is not None
    x0, y0, x1, y1 = pixel_box(token.bbox, size)
    wx0, wy0, wx1, wy1 = pixel_box(token.word_bbox, size)
    m = _STRIP_MARGIN_PX
    strips = []
    if wx0 > x0:
        strips.append((x0, y0 - m, wx0, y1 + m))
    if wx1 < x1:
        strips.append((wx1, y0 - m, x1, y1 + m))
    if wy0 > y0:
        strips.append((x0 - m, y0, x1 + m, wy0))
    if wy1 < y1:
        strips.append((x0 - m, wy1, x1 + m, y1))
    return strips


def _redact_one_word(text: str, angle: int) -> tuple[Token, Image.Image, Image.Image]:
    pdf = _one_word_pdf(text, angle)
    page = extract_tokens(pdf)[0]
    assert [token.text for token in page.tokens] == [text]
    image = render_page(pdf, 1)
    result = redact(image, [blank_bbox(page, [0])])  # 기본 PAD_RATIO
    return page.tokens[0], image, result.image


@pytest.mark.parametrize("text", ["Variance:", "end.", "(word)", "sample):"])
def test_default_padding_keeps_punctuation_and_hides_word(text: str) -> None:
    # 12pt의 패딩(1.8pt)이 글자와 부호 사이 여백(약 1pt)보다 커서, 전에는 부호 잉크가 줄었다.
    token, before, after = _redact_one_word(text, 0)
    strips = _punctuation_strips(token, before.size)
    punctuation = sum(_dark_count(before.crop(strip)) for strip in strips)
    assert punctuation > 0
    assert sum(_dark_count(after.crop(strip)) for strip in strips) == punctuation
    assert token.word_bbox is not None
    assert _dark_count(after.crop(pixel_box(token.word_bbox, after.size))) == 0


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_rotated_word_bbox_and_redaction(angle: int) -> None:
    # "(word):"는 읽는 방향의 앞에 "(", 뒤에 "):"가 붙는다. 회전해도 그 축에서만 줄어야 한다.
    token, before, after = _redact_one_word("(word):", angle)
    assert token.word_bbox is not None
    box, word = token.bbox, token.word_bbox
    along = (0, 2) if angle in (0, 180) else (1, 3)  # 글자 진행 축의 (시작, 끝) 인덱스
    across = (1, 3) if angle in (0, 180) else (0, 2)
    assert all(word[i] == pytest.approx(box[i]) for i in across)
    assert word[along[0]] > box[along[0]] and word[along[1]] < box[along[1]]
    # 앞 부호 "("는 끝 부호 "):"보다 좁다. 화면에서 앞이 어느 쪽인지는 방향에 따라 바뀐다.
    first, last = (0, 1) if angle in (0, 90) else (1, 0)
    gaps = (word[along[0]] - box[along[0]], box[along[1]] - word[along[1]])
    assert gaps[first] < gaps[last]
    strips = _punctuation_strips(token, before.size)
    punctuation = sum(_dark_count(before.crop(strip)) for strip in strips)
    assert punctuation > 0
    assert sum(_dark_count(after.crop(strip)) for strip in strips) == punctuation
    assert _dark_count(after.crop(pixel_box(word, after.size))) == 0
