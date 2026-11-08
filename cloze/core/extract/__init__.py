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


def extract_tokens(pdf: bytes) -> list[PageTokens]:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return [_page_tokens(no, page) for no, page in enumerate(document.pages, start=1)]


def _page_tokens(page_no: int, page: Page) -> PageTokens:
    width, height = float(page.width), float(page.height)
    words = page.extract_words(extra_attrs=["size", "fontname"])
    ordered = sorted(_with_line_ids(words), key=lambda item: (item[0], float(item[1]["x0"])))
    tokens = [
        _token(token_id, line_id, word, width, height)
        for token_id, (line_id, word) in enumerate(ordered)
    ]
    info = PageInfo(page_no=page_no, width_pt=width, height_pt=height)
    return PageTokens(page=info, tokens=tokens)


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


def _token(token_id: int, line_id: int, word: dict[str, Any], width: float, height: float) -> Token:
    return Token(
        id=token_id,
        text=str(word["text"]),
        bbox=_normalized_bbox(word, width, height),
        line_id=line_id,
        font_size=float(word["size"]),
        fontname=str(word["fontname"]),
    )


def _normalized_bbox(word: dict[str, Any], width: float, height: float) -> BBox:
    return (
        _unit(float(word["x0"]) / width),
        _unit(float(word["top"]) / height),
        _unit(float(word["x1"]) / width),
        _unit(float(word["bottom"]) / height),
    )


def _unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)
