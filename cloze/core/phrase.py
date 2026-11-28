"""구 빈칸의 규칙(#47, #130). 규칙 선택기, Gemini 선택기, 게이트가 같이 쓴다.

칸 하나는 토큰 하나이거나, 같은 줄에서 id가 바로 이어진 토큰 MAX_PHRASE_TOKENS개까지다.
구는 문장 조각이 아니어야 한다: 사이에 문장 부호가 없고, 어느 어절에도 조사나 PHRASE_ENDINGS_KO의
어미가 붙지 않는다. 한 단어 칸에는 이 조건을 적용하지 않는다.
models와 filter만 불러오고 둘 다 선택기·게이트를 부르지 않으므로, 어느 쪽에서 불러도
순환 import가 없다.
"""

from collections.abc import Sequence
from itertools import pairwise
from typing import Literal

from cloze.core.filter import EDGE_PUNCTUATION, normalize_ko
from cloze.core.models import Token

# [가설] 구 하나에 담을 최대 토큰 수. 이보다 긴 구간은 용어보다 문장·제목으로 보고
# 칸 하나로 묶지 않는다.
MAX_PHRASE_TOKENS = 3
# [가설] 구 안의 어절이 이것으로 끝나면 용어가 아니라 문장 조각으로 본다(#130).
# 하/되 뒤 어미와 존댓말 끝만 둔다. -면·-고·-한처럼 짧은 어미는 화면·광고·권한 같은 명사를
# 잘못 잡아 넣지 않는다. 긴 것부터 비교할 필요는 없다(끝나는지만 본다).
# -기(명사형)는 넣지 않는다: "말하기 평가"처럼 용어 안의 명사형을 잡는다(#131 리뷰).
PHRASE_ENDINGS_KO = tuple(
    "하는 하여 하고 하며 하면 하지 하게 하던 한다 했다 해야 해서 "
    "되는 되어 되고 되며 되면 되지 되게 되던 된다 됐다 돼야 돼서 "
    "니다".split()
)
# [가설] 끝 글자가 조사(도·가·의·로)처럼 보이지만 명사의 일부인 끝말. 어절이 이것으로 끝나면
# 조사가 붙은 것으로 보지 않는다. 어절 자체가 이것으로 끝날 때만이라 "복잡도가"는 여전히 조사다.
# 테스트용 강의자료 8개에서 셌을 때 구 후보 안의 -가·-의·-로는 대부분 조사였다(가 ~315 대 ~6,
# 의 ~740 대 6, 로 ~385 대 16). -도는 반반(42 대 40)이었다. 그래서 조사 자체를 빼지 않고 명사
# 끝말만 예외로 둔다. 목록에 없는 명사는 알려진 한계다(selection-and-quality-gate.md 3절).
# "정의"는 "결정의"(결정+의)도 잡으므로 낱말 전체("재정의", "미정의")로 둔다.
NOUN_ENDINGS_KO = tuple(
    "복잡도 정확도 유사도 결합도 응집도 정밀도 심각도 충실도 난이도 연관도 흐름도 신뢰도 민감도 "
    "중요도 완성도 의존도 해상도 속도 빈도 밀도 "
    "전문가 분석가 평가 "
    "주의 재정의 미정의 "
    "회로 경로 마이크로 매크로 히어로".split()
)

type PhraseProblem = Literal["length", "not_consecutive", "crosses_line", "phrase_break"]


def adjacent(left: Token, right: Token) -> bool:
    """right가 같은 줄에서 left 바로 다음 토큰인가."""
    return right.id == left.id + 1 and right.line_id == left.line_id


def contiguous(tokens: Sequence[Token]) -> bool:
    """모든 이웃 토큰이 같은 줄에서 바로 이어지는가. 길이는 보지 않는다."""
    return all(adjacent(left, right) for left, right in pairwise(tokens))


def inflected(token: Token) -> bool:
    """앞뒤 부호를 뗀 어절에 조사(normalize_ko)나 PHRASE_ENDINGS_KO의 어미가 붙었는가."""
    word = token.text.strip(EDGE_PUNCTUATION)
    if normalize_ko(word) != word and not word.endswith(NOUN_ENDINGS_KO):
        return True
    return any(word.endswith(ending) and len(word) > len(ending) for ending in PHRASE_ENDINGS_KO)


def splits(left: Token, right: Token) -> bool:
    """left와 right 사이에서 구가 끊기는가.

    left가 문장 부호로 끝나거나 right가 여는 부호로 시작하면 끊는다. 쉼표를 넘어 묶으면 상자가
    부호까지 덮는다. left에 조사·어미가 붙어도 끊는다("데이터를 학습한다"를 막는다).
    """
    marks = tuple(EDGE_PUNCTUATION)
    return left.text.endswith(marks) or right.text.startswith(marks) or inflected(left)


def broken(tokens: Sequence[Token]) -> bool:
    """두 토큰 이상인 구가 문장 조각인가: 사이에서 끊기거나 끝 어절에 조사·어미가 붙음.

    끝 어절도 보므로 "딥러닝 모델이며"를 막는다. 한 단어 칸은 늘 False다.
    """
    if len(tokens) < 2:
        return False
    return any(splits(left, right) for left, right in pairwise(tokens)) or inflected(tokens[-1])


def phrase_problem(tokens: Sequence[Token]) -> PhraseProblem | None:
    """칸 하나로 묶을 수 없는 이유. 묶을 수 있으면 None.

    Gemini 선택기가 재시도 때 무엇을 고칠지 알리도록 이유를 나눈다. id가 이어지는지를 먼저 보고,
    모양이 맞을 때만 문장 조각인지 본다.
    """
    if not 1 <= len(tokens) <= MAX_PHRASE_TOKENS:
        return "length"
    if any(right.id != left.id + 1 for left, right in pairwise(tokens)):
        return "not_consecutive"
    if any(right.line_id != left.line_id for left, right in pairwise(tokens)):
        return "crosses_line"
    if broken(tokens):
        return "phrase_break"
    return None


def is_phrase(tokens: Sequence[Token]) -> bool:
    """칸 하나로 묶을 수 있는 토큰인가: 모양이 맞고 문장 조각이 아님."""
    return phrase_problem(tokens) is None
