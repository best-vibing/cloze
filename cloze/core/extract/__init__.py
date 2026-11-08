"""PDF에서 토큰을 뽑는다. 좌표 규약은 docs/design/pdf-extraction.md 2절이다."""

import io
from typing import Any

import pdfplumber
from pdfplumber.page import Page

from cloze.core.models import BBox, PageInfo, PageTokens, Token

# 렌더링 크기·이미지 형식·좌표 계산이 바뀌면 올린다(pdf-extraction.md 3절).
EXTRACT_VERSION = 1
# [가설] top 차이가 이 값(pt) 이하이면 같은 줄로 본다.
LINE_TOLERANCE_PT = 3.0

type Frame = tuple[float, float, float, float]  # x0, top, x1, bottom (pt)


def extract_tokens(pdf: bytes) -> list[PageTokens]:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return [_page_tokens(no, page) for no, page in enumerate(document.pages, start=1)]


def _page_tokens(page_no: int, page: Page) -> PageTokens:
    frame = _visible_frame(page)
    # 보이는 영역과 겹치는 글자만 남긴다. 좌표는 원래 페이지 기준 그대로다.
    words = page.crop(frame).extract_words(extra_attrs=["size", "fontname"])
    ordered = sorted(_with_line_ids(words), key=lambda item: (item[0], float(item[1]["x0"])))
    tokens = [
        _token(token_id, line_id, word, frame) for token_id, (line_id, word) in enumerate(ordered)
    ]
    width, height = frame[2] - frame[0], frame[3] - frame[1]
    info = PageInfo(page_no=page_no, width_pt=width, height_pt=height)
    return PageTokens(page=info, tokens=tokens)


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


def _with_line_ids(words: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """top이 가까운 단어끼리 같은 줄로 묶는다. 기준은 그 줄의 첫 단어다."""
    line_id = -1
    line_top = float("-inf")
    grouped: list[tuple[int, dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: float(item["top"])):
        top = float(word["top"])
        if top - line_top > LINE_TOLERANCE_PT:
            line_id += 1
            line_top = top
        grouped.append((line_id, word))
    return grouped


def _token(token_id: int, line_id: int, word: dict[str, Any], frame: Frame) -> Token:
    return Token(
        id=token_id,
        text=str(word["text"]),
        bbox=_normalized_bbox(word, frame),
        line_id=line_id,
        font_size=float(word["size"]),
        fontname=str(word["fontname"]),
    )


def _normalized_bbox(word: dict[str, Any], frame: Frame) -> BBox:
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
