"""Find high-confidence non-learning regions using the extracted text layer."""

import math
import re
import unicodedata

from cloze.core.models import PageTokens, Token
from cloze.core.question_region import slide_boundaries

type Line = list[Token]

COVER = {"cover", "title slide", "표지"}
CONTENTS = {"contents", "table of contents", "agenda", "목차"}
CLOSING = {"end", "the end", "thank you", "thanks", "감사합니다", "수고하셨습니다", "q&a"}
ASSIGNMENT = {"homework", "assignment", "과제", "과제 안내"}
_ADMIN = re.compile(
    r"(?:due|deadline|submit (?:via|on|to)|제출 기한|제출 마감|제출 방법)(?:\s*:.*)"
)
_ENTRY_PREFIX = re.compile(r"^(?:[•·▪–-]\s*|\d+[.)]\s+)")
_PROSE_OR_CODE = re.compile(r"[;=:{}/!?]|[.!?]$")
_PRESENTER = re.compile(r"(?:professor|prof\.?|instructor|by)\s+.+|.+\s+교수(?:님)?")


def noncontent_ids(
    page: PageTokens,
    lines: list[Line],
    repeated: set[int],
    cover_metadata: set[int],
    *,
    first: bool,
    last: bool,
) -> set[int]:
    if page.page.source != "textlayer" or not lines or not 0 < page.page.height_pt < math.inf:
        return set()
    if not all(_reliable(token) for token in page.tokens):
        return set()
    ordered = sorted(
        (line for line in lines if not all(token.id in repeated for token in line)), key=_top
    )
    if not ordered:
        return set()
    boundaries = slide_boundaries(page, repeated)
    internal = next((top for top in boundaries if 0.35 <= top <= 0.65), None)
    if internal is not None:
        upper = [line for line in ordered if _bottom(line) <= internal]
        has_lower = any(_top(line) > internal for line in ordered)
        excluded = (
            upper
            if first
            and has_lower
            and upper
            and _is_cover(upper, page.page.height_pt * internal, internal, cover_metadata)
            else []
        )
    else:
        excluded = (
            ordered if _is_noncontent(ordered, page, cover_metadata, first=first, last=last) else []
        )
    return {token.id for line in excluded for token in line}


def _reliable(token: Token) -> bool:
    left, top, right, bottom = token.bbox
    return (
        0 <= left < right <= 1
        and 0 <= top < bottom <= 1
        and 0 < token.font_size < math.inf
        and bool(token.text.strip())
        and "\ufffd" not in token.text
    )


def _is_noncontent(
    lines: list[Line], page: PageTokens, cover_metadata: set[int], *, first: bool, last: bool
) -> bool:
    heading, *body = lines
    marker = _marker(heading)
    prominent = _size(heading) >= 0.026 * page.page.height_pt
    if _code_font(heading):
        return False
    if last and marker in CLOSING:
        return not body and prominent and _centered(heading) and 0.20 <= _top(heading) <= 0.78
    if marker in CONTENTS:
        return prominent and _top(heading) <= 0.4 and _is_outline(heading, body)
    if marker in ASSIGNMENT:
        return prominent and _is_assignment(heading, body)
    return first and _is_cover(lines, page.page.height_pt, 1.0, cover_metadata)


def _is_cover(lines: list[Line], height: float, bottom: float, cover_metadata: set[int]) -> bool:
    heading, *details = lines
    marker = _marker(heading)
    if marker in CONTENTS | CLOSING | ASSIGNMENT:
        return False
    if len(details) > 2 or sum(map(len, lines)) > 20:
        return False
    if not 0.12 * bottom <= _top(heading) <= 0.68 * bottom:
        return False
    if _bottom(lines[-1]) - _top(heading) > 0.28 * bottom:
        return False
    if not _cover_alignment(lines, bottom):
        return False
    if marker in COVER:
        return not details and _size(heading) >= 0.026 * height and _short_label(heading)
    return _inferred_cover(heading, details, height, cover_metadata)


def _inferred_cover(heading: Line, details: list[Line], height: float, metadata: set[int]) -> bool:
    if len(heading) < 2 or _size(heading) < 0.042 * height:
        return False
    if not any(
        _PRESENTER.fullmatch(_text(line)) or all(token.id in metadata for token in line)
        for line in details
    ):
        return False
    return _short_label(heading) and all(
        _short_label(line) and _size(line) <= 0.78 * _size(heading) for line in details
    )


def _cover_alignment(lines: list[Line], bottom: float) -> bool:
    if all(_centered(line) for line in lines):
        return True
    lefts = [min(token.bbox[0] for token in line) for line in lines]
    return bottom < 1.0 and len(lines) == 3 and max(lefts) - min(lefts) <= 0.04


def _is_outline(heading: Line, body: list[Line]) -> bool:
    if not body:
        return _centered(heading)
    if not 2 <= len(body) <= 10 or not all(_short_label(line) for line in body):
        return False
    return all(
        _size(line) <= 0.86 * _size(heading) and _top(line) - _bottom(previous) <= 0.13
        for previous, line in zip([heading, *body[:-1]], body, strict=True)
    )


def _is_assignment(heading: Line, body: list[Line]) -> bool:
    if not body:
        return _centered(heading) or _top(heading) <= 0.35
    return len(body) <= 3 and all(
        _ADMIN.fullmatch(_ENTRY_PREFIX.sub("", _text(line)))
        and len(_text(line).split(":", 1)[1].split()) <= 4
        and not _PROSE_OR_CODE.search(_text(line).split(":", 1)[1].strip())
        and _size(line) <= 0.86 * _size(heading)
        for line in body
    )


def _short_label(line: Line) -> bool:
    label = _ENTRY_PREFIX.sub("", _text(line))
    return (
        0 < len(label.split()) <= 9
        and len(label) <= 85
        and not _PROSE_OR_CODE.search(label)
        and not _code_font(line)
    )


def _text(line: Line) -> str:
    text = " ".join(token.text for token in sorted(line, key=lambda token: token.bbox[0]))
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _marker(line: Line) -> str:
    return re.sub(r"\s*&\s*", "&", _text(line)).rstrip(".!:")


def _code_font(line: Line) -> bool:
    return any(
        name in token.fontname.casefold()
        for token in line
        for name in ("courier", "mono", "consolas")
    )


def _centered(line: Line) -> bool:
    left = min(token.bbox[0] for token in line)
    right = max(token.bbox[2] for token in line)
    return abs((left + right) / 2 - 0.5) <= 0.13


def _top(line: Line) -> float:
    return min(token.bbox[1] for token in line)


def _bottom(line: Line) -> float:
    return max(token.bbox[3] for token in line)


def _size(line: Line) -> float:
    return min(token.font_size for token in line)
