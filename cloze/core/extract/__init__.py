"""PDF에서 토큰을 뽑는다. 좌표 규약은 docs/design/pdf-extraction.md 2절이다."""

import io
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Literal

import pdfplumber
from pdfplumber.page import Page
from pdfplumber.utils.text import WordExtractor

from cloze.core.models import BBox, PageInfo, PageTokens, Token

# 렌더링 크기·이미지 형식·좌표 계산·단어 나누기가 바뀌면 올린다(pdf-extraction.md 3절).
EXTRACT_VERSION = 2
# [가설] 읽는 방향으로 본 top 차이가 이 값(pt) 이하이면 같은 줄로 본다.
LINE_TOLERANCE_PT = 3.0
# [가설] 글자 사이 간격이 글자 크기의 이 비율을 넘으면 다른 단어로 본다(pdf-extraction.md 2절).
# 한국어 PDF는 띄어쓰기를 공백 글자 없이 0.28~0.30em 간격으로만 저장하는 경우가 있어,
# pdfplumber 기본값 3pt(10pt 글자에서 0.3em)로는 어절이 붙는다. 어절 안 글자 간격은 0이다.
X_TOLERANCE_RATIO = 0.15

type Frame = tuple[float, float, float, float]  # x0, top, x1, bottom (pt)
type Word = dict[str, Any]
type Dir = Literal["ltr", "rtl", "ttb", "btt"]

EXTRA_ATTRS = ["size", "fontname"]
# 화면에서 본 글자 진행 방향(도, 시계 방향)별 (글자 방향, 줄이 쌓이는 방향).
_WORD_DIRECTIONS: dict[int, tuple[Dir, Dir]] = {
    0: ("ltr", "ttb"),
    90: ("ttb", "rtl"),
    180: ("rtl", "btt"),
    270: ("btt", "ltr"),
}
# 화면 좌표의 박스를 "읽기 좌표"(글이 왼쪽→오른쪽, 줄이 위→아래로 가는 좌표)로 바꾼다.
_READING: dict[int, Callable[[float, float, float, float], Frame]] = {
    0: lambda x0, top, x1, bottom: (x0, top, x1, bottom),
    90: lambda x0, top, x1, bottom: (top, -x1, bottom, -x0),
    180: lambda x0, top, x1, bottom: (-x1, -bottom, -x0, -top),
    270: lambda x0, top, x1, bottom: (-bottom, x0, -top, x1),
}


def extract_tokens(pdf: bytes) -> list[PageTokens]:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return [_page_tokens(no, page) for no, page in enumerate(document.pages, start=1)]


def _page_tokens(page_no: int, page: Page) -> PageTokens:
    frame = _visible_frame(page)
    # 보이는 영역과 겹치는 글자만 남긴다. 좌표는 원래 페이지 기준 그대로다.
    chars = [_with_font_size(char) for char in page.crop(frame).chars]
    ordered = _ordered_words(chars)
    tokens = [
        _token(token_id, line_id, word, frame) for token_id, (line_id, word) in enumerate(ordered)
    ]
    width, height = frame[2] - frame[0], frame[3] - frame[1]
    info = PageInfo(page_no=page_no, width_pt=width, height_pt=height)
    return PageTokens(page=info, tokens=tokens)


def _word_extractor(direction: int) -> WordExtractor:
    """0·180도 글자는 pdfplumber가 upright로, 90·270도 글자는 rotated로 다룬다."""
    char_dir, line_dir = _WORD_DIRECTIONS[direction]
    if direction in (90, 270):
        # 세로 글은 pdfplumber가 x·y 허용치를 바꿔 쓴다: 글자 사이 간격은 y 허용치로,
        # 줄 판정은 x 허용치로 본다. 그래서 비율은 y 쪽에 주고 줄 판정은 기본값(3pt)으로 둔다.
        return WordExtractor(
            extra_attrs=EXTRA_ATTRS,
            y_tolerance_ratio=X_TOLERANCE_RATIO,
            char_dir_rotated=char_dir,
            line_dir_rotated=line_dir,
        )
    return WordExtractor(
        extra_attrs=EXTRA_ATTRS,
        x_tolerance_ratio=X_TOLERANCE_RATIO,
        char_dir=char_dir,
        line_dir=line_dir,
    )


def _visible_frame(page: Page) -> Frame:
    """pypdfium2가 그리는 영역: CropBox와 MediaBox의 교집합(pt, 위쪽 기준, 회전 반영)."""
    crop = [float(value) for value in page.cropbox]
    full = [float(value) for value in page.bbox]
    return (
        max(crop[0], full[0]),
        max(crop[1], full[1]),
        min(crop[2], full[2]),
        min(crop[3], full[3]),
    )


def _with_font_size(char: dict[str, Any]) -> dict[str, Any]:
    """옆으로 누운 글자는 pdfplumber의 size가 글자 폭이라, 가로 길이(= 글자 크기)로 바꾼다."""
    if char["upright"]:
        return char
    return {**char, "size": round(float(char["x1"]) - float(char["x0"]), 2)}


def _ordered_words(chars: list[dict[str, Any]]) -> list[tuple[int, Word]]:
    """글자를 진행 방향별로 나눠 단어를 만들고, 글자가 많은 방향(같으면 각도가 작은 방향)부터
    이어 붙인다. line_id는 앞 그룹에 이어서 0부터 연속으로 매긴다."""
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for char in chars:
        groups[_char_direction(char)].append(char)
    ordered: list[tuple[int, Word]] = []
    for direction in sorted(groups, key=lambda key: (-len(groups[key]), key)):
        next_line = max((line for line, _ in ordered), default=-1) + 1
        ordered += [
            (next_line + line, word) for line, word in _group_words(direction, groups[direction])
        ]
    return ordered


def _group_words(direction: int, chars: list[dict[str, Any]]) -> list[tuple[int, Word]]:
    """한 방향의 글자로 단어를 만들고, 그 방향의 읽기 좌표로 줄을 묶어 정렬한다."""
    words = _word_extractor(direction).extract_words(chars)
    return _in_reading_order([(_READING[direction](*_box(word)), word) for word in words])


def _char_direction(char: dict[str, Any]) -> int:
    # matrix의 (a, b)는 PDF 좌표(위가 +)에서 글자가 나아가는 방향이다.
    a, b = (float(value) for value in char["matrix"][:2])
    if abs(a) >= abs(b):
        return 0 if a > 0 else 180
    return 90 if b < 0 else 270


def _box(word: Word) -> Frame:
    return (float(word["x0"]), float(word["top"]), float(word["x1"]), float(word["bottom"]))


def _in_reading_order(placed: list[tuple[Frame, Word]]) -> list[tuple[int, Word]]:
    """읽기 좌표의 top이 가까운 단어끼리 같은 줄로 묶고, 줄 → 읽기 x 순서로 정렬한다."""
    line_id = -1
    line_top = float("-inf")
    lined: list[tuple[int, float, Word]] = []
    for box, word in sorted(placed, key=lambda item: item[0][1]):
        if box[1] - line_top > LINE_TOLERANCE_PT:
            line_id += 1
            line_top = box[1]
        lined.append((line_id, box[0], word))
    return [(line, word) for line, _, word in sorted(lined, key=lambda item: item[:2])]


def _token(token_id: int, line_id: int, word: Word, frame: Frame) -> Token:
    return Token(
        id=token_id,
        text=str(word["text"]),
        bbox=_normalized_bbox(word, frame),
        line_id=line_id,
        font_size=float(word["size"]),
        fontname=str(word["fontname"]),
    )


def _normalized_bbox(word: Word, frame: Frame) -> BBox:
    """보이는 영역의 좌상단을 원점으로 0~1로 바꾼다."""
    left, top, right, bottom = frame
    width, height = right - left, bottom - top
    return (
        _unit((float(word["x0"]) - left) / width),
        _unit((float(word["top"]) - top) / height),
        _unit((float(word["x1"]) - left) / width),
        _unit((float(word["bottom"]) - top) / height),
    )


def _unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)
