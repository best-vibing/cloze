"""자동 자료 분석을 위한 모델 독립적인 대표 페이지 선정.

PR #15의 모델은 수정하지 않고 읽기 전용 Protocol로 토큰 필드에 접근한다.
호출자는 추출된 페이지 순서와 페이지별 텍스트를 제공한다.
"""

from collections.abc import Callable, Sequence
from math import isfinite
from typing import Protocol

FRONT_PAGE_COUNT = 2
SPREAD_PAGE_COUNT = 5
# PDF 추출 설계 4절의 20자 기준을 임시로 재사용한다. 실자료 보정이 필요하다.
MIN_PAGE_TEXT_CHARS = 20
# 임시 문자 예산이며 Gemini 토큰 상한이 아니다. 실자료 측정 후 조정한다.
MAX_ANALYSIS_INPUT_CHARS = 12_000


class AnalysisToken(Protocol):
    """PR #15 Token의 필요한 필드만 읽는 경계. 별도 데이터 모델이 아니다."""

    @property
    def text(self) -> str:
        """토큰의 원문을 반환한다."""
        ...

    @property
    def line_id(self) -> int:
        """페이지 내부 줄 식별자를 반환한다."""
        ...

    @property
    def font_size(self) -> float | None:
        """글자 크기를 반환한다. 메타데이터 누락 시 None을 허용한다."""
        ...


def _spread_indices(length: int) -> list[int]:
    """양 끝을 포함해 최대 다섯 위치를 결정적으로 고른다."""
    if length <= SPREAD_PAGE_COUNT:
        return list(range(length))
    return [index * (length - 1) // (SPREAD_PAGE_COUNT - 1) for index in range(SPREAD_PAGE_COUNT)]


def select_representative_pages[T](pages: Sequence[T], text_of: Callable[[T], str]) -> list[T]:
    """텍스트가 충분한 앞 두 페이지와 나머지의 균등 표본을 반환한다.

    Args:
        pages: 문서 순서의 전체 페이지. 각 원소는 서로 다른 페이지여야 한다.
        text_of: 페이지의 추출 텍스트를 반환하는 순수 함수.

    Returns:
        입력 순서를 유지한 최대 일곱 페이지. 공백을 제외한 글자가 임시 기준인
        20자 미만인 페이지는 제외하며, 충분한 페이지가 없으면 빈 목록이다.
        같은 입력 위치를 중복 선택하지 않는다. 입력은 변경하지 않는다.
    """
    eligible = [page for page in pages if _has_sufficient_text(text_of(page))]
    front = eligible[:FRONT_PAGE_COUNT]
    remaining = eligible[FRONT_PAGE_COUNT:]
    return front + [remaining[index] for index in _spread_indices(len(remaining))]


def _has_sufficient_text(text: str) -> bool:
    """공백을 제외한 글자 수로 텍스트 충분 여부를 판단한다."""
    return sum(not char.isspace() for char in text) >= MIN_PAGE_TEXT_CHARS


def _usable_font_size(token: AnalysisToken) -> float:
    """비어 있지 않은 토큰의 양수 유한 글자 크기를 반환한다.

    Args:
        token: 추출된 토큰.

    Returns:
        유효한 글자 크기. 누락, 비유한 값, 비양수 또는 빈 글이면 0.
    """
    size = token.font_size
    if not token.text.strip() or size is None:
        return 0.0
    return size if isfinite(size) and size > 0 else 0.0


def extract_heading_lines(tokens: Sequence[AnalysisToken]) -> list[str]:
    """한 페이지에서 최대 글자 크기를 포함하는 줄 전체를 추출한다.

    Args:
        tokens: 한 페이지의 원문 읽기 순서 토큰. line_id는 페이지 내부 식별자다.

    Returns:
        최대 유효 크기와 동률인 모든 줄을 첫 등장 순서로 반환한다. 같은 줄의
        작은 글씨와 크기가 누락된 토큰도 포함하며 토큰 순서는 바꾸지 않는다.
        유효한 크기가 없으면 빈 목록이다. 제목 여부는 글자 크기 휴리스틱이다.
    """
    sizes = [_usable_font_size(token) for token in tokens]
    largest = max(sizes, default=0.0)
    if largest == 0:
        return []
    heading_ids = {
        token.line_id for token, size in zip(tokens, sizes, strict=True) if size == largest
    }
    return _join_heading_lines(tokens, heading_ids)


def _join_heading_lines(tokens: Sequence[AnalysisToken], heading_ids: set[int]) -> list[str]:
    """선정된 줄을 추출 순서대로 공백으로 연결한다.

    Args:
        tokens: 한 페이지의 원문 순서 토큰.
        heading_ids: 선택한 페이지 내부 줄 식별자.

    Returns:
        줄과 토큰의 최초 등장 순서를 유지한 문자열 목록.
    """
    lines: dict[int, list[str]] = {}
    for token in tokens:
        if token.line_id in heading_ids and token.text.strip():
            lines.setdefault(token.line_id, []).append(token.text)
    return [" ".join(words) for words in lines.values()]


def limit_analysis_input(text: str, max_chars: int = MAX_ANALYSIS_INPUT_CHARS) -> str:
    """Gemini에 전달할 완성된 입력의 앞부분을 문자 예산 안에 유지한다.

    Args:
        text: 지시문과 자료를 조합한 전체 입력. 호출자는 필수 지시문을 앞에 둔다.
        max_chars: Python len 기준 유니코드 코드 포인트 상한. 0은 빈 입력이다.

    Returns:
        상한 이내라면 원문, 초과하면 앞 max_chars 글자. 실제 Gemini 토큰 수를
        계산하거나 보장하지 않는다. 뒤쪽 문맥과 마지막 줄 일부가 잘릴 수 있다.

    Raises:
        TypeError: 상한이 정수가 아니거나 bool인 경우.
        ValueError: 상한이 음수인 경우.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_chars는 정수여야 합니다")
    if max_chars < 0:
        raise ValueError("max_chars는 0 이상이어야 합니다")
    return text[:max_chars]
