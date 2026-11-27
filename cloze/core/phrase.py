"""구 빈칸의 모양 규칙(#47). 규칙 선택기, Gemini 선택기, 게이트가 같이 쓴다.

칸 하나는 토큰 하나이거나, 같은 줄에서 id가 바로 이어진 토큰 MAX_PHRASE_TOKENS개까지다.
모델만 불러오므로 선택기와 게이트 어느 쪽에서 불러도 순환 import가 생기지 않는다.
"""

from collections.abc import Sequence
from itertools import pairwise
from typing import Literal

from cloze.core.models import Token

MAX_PHRASE_TOKENS = 3

type PhraseProblem = Literal["length", "not_consecutive", "crosses_line"]


def adjacent(left: Token, right: Token) -> bool:
    """right가 같은 줄에서 left 바로 다음 토큰인가."""
    return right.id == left.id + 1 and right.line_id == left.line_id


def contiguous(tokens: Sequence[Token]) -> bool:
    """모든 이웃 토큰이 같은 줄에서 바로 이어지는가. 길이는 보지 않는다."""
    return all(adjacent(left, right) for left, right in pairwise(tokens))


def phrase_problem(tokens: Sequence[Token]) -> PhraseProblem | None:
    """칸 하나로 묶을 수 없는 이유. 묶을 수 있으면 None.

    Gemini 선택기가 재시도 때 무엇을 고칠지 알리도록 이유를 나눈다. id가 이어지는지를 먼저 본다.
    """
    if not 1 <= len(tokens) <= MAX_PHRASE_TOKENS:
        return "length"
    if any(right.id != left.id + 1 for left, right in pairwise(tokens)):
        return "not_consecutive"
    if any(right.line_id != left.line_id for left, right in pairwise(tokens)):
        return "crosses_line"
    return None


def is_phrase(tokens: Sequence[Token]) -> bool:
    """칸 하나로 묶을 수 있는 토큰인가: 1~MAX_PHRASE_TOKENS개이고 같은 줄에서 이어짐."""
    return phrase_problem(tokens) is None
