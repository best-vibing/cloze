"""Find high-confidence non-learning regions using the extracted text layer."""

import math
import re
import unicodedata

from cloze.core.models import PageTokens, Token
from cloze.core.question_region import slide_boundaries

type Line = list[Token]

COVER = {"cover", "title slide", "표지"}
CLOSING = {"end", "the end", "thank you", "thanks", "감사합니다", "수고하셨습니다"}
_PROSE_OR_CODE = re.compile(r"[;=:{}/!?]|[.!?]$")
_PRESENTER = re.compile(r"(?:professor|prof\.?|instructor|by)[ :]+.+|.+\s+교수(?:님)?")


def noncontent_ids(
    page: PageTokens,
    lines: list[Line],
    repeated: set[int],
    cover_metadata: set[int],
    *,
    first: bool,
    last: bool,
) -> set[int]:
    if not (first or last) or page.page.source != "textlayer" or page.page.height_pt <= 0:
        return set()
    ordered = sorted(
        (line for line in lines if not all(token.id in repeated for token in line)), key=_top
    )
    if first and _is_cover(ordered, page.page.height_pt, cover_metadata, mixed=False):
        return {token.id for line in ordered for token in line}
    upper, lower = _split(page, ordered, repeated)
    excluded = []
    if (
        first
        and len(lower) >= 2
        and _is_cover(upper, page.page.height_pt, cover_metadata, mixed=True)
    ):
        excluded.extend(upper)
    if last and _is_closing(lower or upper, page.page.height_pt, mixed=bool(lower)):
        excluded.extend(lower or upper)
    return {token.id for line in excluded for token in line}


def _split(
    page: PageTokens, lines: list[Line], repeated: set[int]
) -> tuple[list[Line], list[Line]]:
    boundary = next((top for top in slide_boundaries(page, repeated) if 0.35 <= top <= 0.65), None)
    if boundary is None:
        boundary = 0.5
        if not all(_bottom(line) <= 0.46 or _top(line) >= 0.54 for line in lines):
            return lines, []
    upper = [line for line in lines if _bottom(line) <= boundary]
    lower = [line for line in lines if _top(line) > boundary]
    if upper and lower and len(upper) + len(lower) == len(lines):
        return upper, lower
    return lines, []


def _reliable(token: Token) -> bool:
    left, top, right, bottom = token.bbox
    return (
        0 <= left < right <= 1
        and 0 <= top < bottom <= 1
        and 0 < token.font_size < math.inf
        and bool(token.text.strip())
        and "\ufffd" not in token.text
    )


def _is_closing(lines: list[Line], height: float, *, mixed: bool) -> bool:
    if len(lines) != 1:
        return False
    line = lines[0]
    return (
        all(map(_reliable, line))
        and _marker(line) in CLOSING
        and not _code_font(line)
        and _centered(line)
        and _size(line) >= 0.026 * height * (0.5 if mixed else 1.0)
    )


def _is_cover(lines: list[Line], height: float, metadata: set[int], *, mixed: bool) -> bool:
    if not lines or not all(_reliable(token) for line in lines for token in line):
        return False
    heading = max(lines, key=_size)
    region_height = height * (0.5 if mixed else 1.0)
    if _marker(heading) in COVER:
        return len(lines) == 1 and _centered(heading) and _size(heading) >= 0.026 * region_height
    details = [line for line in lines if line is not heading]
    return (
        _size(heading) >= 0.042 * region_height
        and _short_label(heading)
        and (not mixed or len(details) <= 2 or all(_centered(line) for line in lines))
        and 1 <= len(details) <= 6
        and all(_cover_detail(line, heading) for line in details)
        and _cover_evidence(heading, details, metadata, mixed=mixed)
    )


def _cover_evidence(heading: Line, details: list[Line], metadata: set[int], *, mixed: bool) -> bool:
    if any(
        _PRESENTER.fullmatch(_text(line)) or all(token.id in metadata for token in line)
        for line in details
    ):
        return True
    return (
        mixed
        and len(heading) >= 2
        and len(details) == 1
        and _centered(heading)
        and _centered(details[0])
        and _top(details[0]) - _bottom(heading) <= 0.10
        and _top(details[0]) >= _bottom(heading)
    )


def _cover_detail(line: Line, heading: Line) -> bool:
    return _size(line) <= 0.78 * _size(heading) and (
        _short_label(line) or bool(_PRESENTER.fullmatch(_text(line)))
    )


def _short_label(line: Line) -> bool:
    label = _text(line)
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
