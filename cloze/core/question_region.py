"""문제·예시 슬라이드 영역을 찾는다. filter가 이 영역의 토큰을 question_region으로 뺀다(#66).

판별은 쪽이 아니라 슬라이드 영역 단위다. 두 장 모아찍기 PDF는 한 쪽의 위가 문제, 아래가 개념일 수
있어서, 쪽 전체를 빼면 개념 빈칸까지 사라진다.
"""

import re
from collections import defaultdict

from cloze.core.models import PageTokens, Token

# [가설] 줄 첫 토큰이 이 단어이면 문제·예시 표시로 본다. 실제 강의자료로 확인하며 늘린다.
QUESTION_MARKERS = frozenset(
    "example examples exercise exercises problem problems quiz q "
    "문제 예제 예시 연습문제 연습 퀴즈".split()
)
_Q_NUMBERED = re.compile(r"q\d+")  # Q1, Q12
_NUMBER = re.compile(r"\d+[.)]?")  # "문제 1", "Example 2."
# 표시 토큰이 줄 전체이거나, 이 부호로 끝나거나, 다음 토큰이 번호일 때만 맞춘다.
# "for example, ..."처럼 문장 안에서 쓰인 단어는 줄 첫머리에 와도 쉼표로 끝나므로 맞지 않는다.
MARKER_ENDINGS = (":", ".", ")")
_EDGE = ".,;:!?\"'()[]{}<>“”‘’·…"
# [가설] 슬라이드 경계 단서가 없을 때: 표시 줄 아래로, 줄 사이 세로 간격이 앞 줄 높이의
# 이 배수 이하로 이어지는 줄까지만 뺀다(문단 하나). 쪽 전체를 빼지 않는 보수안이다.
PARAGRAPH_GAP_RATIO = 1.5

type Key = tuple[int, int]  # 페이지 번호, 토큰 id
type Line = list[Token]


def question_region_ids(pages: list[PageTokens], header_footer: set[Key]) -> set[Key]:
    """문제 영역에 든 (페이지 번호, 토큰 id)를 모은다. header_footer는 슬라이드 경계 단서다."""
    found: set[Key] = set()
    for page in pages:
        page_no = page.page.page_no
        lines = _lines(page)
        marked = [line for line in lines if is_question_marker(line)]
        if not marked:
            continue
        repeated = {id_ for no, id_ in header_footer if no == page_no}
        boundaries = _boundaries(page, repeated)
        for line in marked:
            region = _region(lines, line, boundaries, repeated)
            found |= {(page_no, token.id) for tokens in region for token in tokens}
    return found


def is_question_marker(line: Line) -> bool:
    """줄 첫 토큰이 문제·예시 표시인가."""
    head = line[0].text.strip(_EDGE).casefold()
    if head not in QUESTION_MARKERS and not _Q_NUMBERED.fullmatch(head):
        return False
    if len(line) == 1 or line[0].text.endswith(MARKER_ENDINGS):
        return True
    return _NUMBER.fullmatch(line[1].text.strip(_EDGE)) is not None


def _lines(page: PageTokens) -> list[Line]:
    by_line: dict[int, Line] = defaultdict(list)
    for token in page.tokens:
        by_line[token.line_id].append(token)
    return sorted(by_line.values(), key=_top)


def _top(line: Line) -> float:
    return min(token.bbox[1] for token in line)


def _bottom(line: Line) -> float:
    return max(token.bbox[3] for token in line)


def _boundaries(page: PageTokens, header_footer_ids: set[int]) -> list[float]:
    """슬라이드 경계: 반복 머리글·바닥글 줄 중 글자가 가장 작은 줄들의 top. 제목이 반복되는 경우를
    바닥글과 구분하기 위해서다. 단서가 없으면 빈 목록이다."""
    repeated = [line for line in _lines(page) if all(t.id in header_footer_ids for t in line)]
    if not repeated:
        return []
    smallest = min(_size(line) for line in repeated)
    return sorted(_top(line) for line in repeated if _size(line) == smallest)


def _size(line: Line) -> float:
    # 같은 글꼴 크기도 PDF마다 8.0400384와 8.0399999처럼 미세하게 다르다. 반올림해 비교한다.
    return round(max(token.font_size for token in line), 1)


def _region(
    lines: list[Line], marked: Line, boundaries: list[float], repeated: set[int]
) -> list[Line]:
    """표시 줄과 함께 뺄 줄들.

    경계가 있고 표시 줄이 슬라이드 영역의 첫 줄(제목 자리)이면 영역 전체를 뺀다. 첫 줄을 셀 때
    반복 머리글·바닥글 줄은 건너뛴다(반복되는 슬라이드 제목 아래의 "Example:"도 제목 자리다).
    표시 줄이 본문 중간에 있으면("Example: learning rate …") 개념 슬라이드일 수 있으므로, 경계가
    없을 때와 같이 표시 줄과 이어지는 문단만 뺀다."""
    if not boundaries:
        return _paragraph(lines, marked)
    lower, upper = _bounds(_top(marked), boundaries)
    slide = [line for line in lines if lower < _top(line) <= upper]
    body = [line for line in slide if not all(token.id in repeated for token in line)]
    if body and body[0] is marked:
        return slide
    return _paragraph(slide, marked)


def _bounds(top: float, boundaries: list[float]) -> tuple[float, float]:
    """top을 품는 (바로 위 경계, 바로 아래 경계]. 경계 줄 자체는 위 영역에 속한다."""
    lower = max((b for b in boundaries if b < top), default=-1.0)
    upper = min((b for b in boundaries if b >= top), default=2.0)
    return lower, upper


def _paragraph(lines: list[Line], marked: Line) -> list[Line]:
    region = [marked]
    for line in lines[lines.index(marked) + 1 :]:
        previous = region[-1]
        if (
            _top(line) - _bottom(previous)
            > (_bottom(previous) - _top(previous)) * PARAGRAPH_GAP_RATIO
        ):
            break
        region.append(line)
    return region
