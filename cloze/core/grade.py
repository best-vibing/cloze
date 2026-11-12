"""입력한 답을 정답과 비교해 칸마다 맞음 / 거의 맞음 / 틀림을 정한다.

판정 기준은 docs/design/grading-and-overlay.md 1절이다.
DB·네트워크·PDF 없이 문자열만 다루는 순수 함수다.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from cloze.core.filter import normalize_ko

type Verdict = Literal["correct", "near", "wrong"]
type BlankType = Literal["text", "number", "term"]

NEAR_MIN_LEN = 5  # [가설] 정답이 이 길이 이상일 때만 near를 준다.
NEAR_MAX_DISTANCE = 1  # [가설] near로 볼 최대 편집 거리.

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class AnswerKey:
    """빈칸 하나의 정답 정보. 서버만 가지고 있고 화면으로 보내지 않는다.

    Attributes:
        answer (str): 정답. 선택된 토큰의 원문이다.
        variants (tuple[str, ...]): 검증을 통과한 약어·동의어.
        type (BlankType): 빈칸 종류. ``"number"`` 면 숫자로도 비교한다.
        answer_group (str | int | None): 같은 값끼리는 입력을 서로 바꿔 넣어도 맞다.
    """

    answer: str
    variants: tuple[str, ...] = ()
    type: BlankType = "term"
    answer_group: str | int | None = None


@dataclass(frozen=True)
class Grading:
    """한 번 제출한 답안의 채점 결과.

    Attributes:
        results (dict[str, Verdict]): 빈칸 id마다 판정.
        score (float): ``correct`` 칸 수 ÷ 전체 칸 수. ``near`` 는 넣지 않는다.
    """

    results: dict[str, Verdict]
    score: float


def normalize(text: str) -> str:
    """비교용으로 글자를 다듬는다. NFKC, 대소문자 무시, 공백 정리, 문장부호 제거.

    숫자에 붙은 부호는 남긴다. 숫자 사이(``3.14`` 의 점, ``1,665`` 의 쉼표)와
    숫자 앞의 ``-``·``.``(``-273``, ``.5``)다. 지우면 ``31.4`` 와 ``3.14``, ``-273`` 과
    ``273`` 이 같아진다.

    Examples:
        >>> normalize("  Ｍitochondria!! ")
        'mitochondria'
        >>> normalize("3.14")
        '3.14'
        >>> normalize("-273")
        '-273'
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    kept = [ch for i, ch in enumerate(text) if not _is_dropped_punct(text, i)]
    return _SPACES.sub(" ", "".join(kept)).strip()


def _is_dropped_punct(text: str, i: int) -> bool:
    if not unicodedata.category(text[i]).startswith("P"):
        return False
    before_digit = i < len(text) - 1 and text[i + 1].isdigit()
    after_digit = i > 0 and text[i - 1].isdigit()
    # 숫자 앞 부호: 글 처음이거나 공백 뒤일 때만. "COVID-19"의 붙임표는 지운다.
    leading_sign = text[i] in "-." and (i == 0 or text[i - 1].isspace())
    return not (before_digit and (after_digit or leading_sign))


def edit_distance(a: str, b: str) -> int:
    """한 글자 넣기·빼기·바꾸기를 몇 번 해야 a가 b가 되는지 센다(레벤슈타인 거리).

    Examples:
        >>> edit_distance("golgy", "golgi")
        1
    """
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _same_text(given: str, expected: str) -> bool:
    """정규화한 뒤 같거나, 양쪽 끝의 조사를 떼면 같은가(조사 목록은 filter와 공유)."""
    g, e = normalize(given), normalize(expected)
    return g != "" and (g == e or normalize_ko(g) == normalize_ko(e))


def _parse_number(text: str) -> Decimal | None:
    cleaned = normalize(text).replace(",", "").replace(" ", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _same_number(given: str, expected: str) -> bool:
    """정답의 마지막 유효숫자 자리의 절반까지 차이를 허용한다([가설]). 예: 3.14 → ±0.005."""
    g, e = _parse_number(given), _parse_number(expected)
    if g is None or e is None:
        return False
    exponent = e.as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    return abs(g - e) <= Decimal(5).scaleb(exponent - 1)


def is_correct(given: str, key: AnswerKey) -> bool:
    """판정 1~3단계: 정답·변형 목록과 같거나, 숫자 칸이면 허용 범위 안인가."""
    if any(_same_text(given, accepted) for accepted in (key.answer, *key.variants)):
        return True
    return key.type == "number" and _same_number(given, key.answer)


def judge(given: str, key: AnswerKey) -> Verdict:
    """빈칸 하나를 판정한다. 위 단계에서 맞으면 아래는 보지 않는다.

    Args:
        given (str): 사용자가 입력한 글자.
        key (AnswerKey): 그 칸의 정답 정보.

    Returns:
        Verdict: ``"correct"``, ``"near"``(점수에는 넣지 않음), ``"wrong"`` 중 하나.

    Examples:
        >>> judge("ATP", AnswerKey("atp"))
        'correct'
        >>> judge("Golgy", AnswerKey("Golgi"))
        'near'
        >>> judge("", AnswerKey("ATP"))
        'wrong'
    """
    if is_correct(given, key):
        return "correct"
    g, e = normalize(given), normalize(key.answer)
    if g and len(e) >= NEAR_MIN_LEN and edit_distance(g, e) <= NEAR_MAX_DISTANCE:
        return "near"
    return "wrong"


def grade(answers: Mapping[str, str], keys: Mapping[str, AnswerKey]) -> Grading:
    """한 세트의 답안을 채점한다. 같은 ``answer_group`` 칸끼리는 입력을 바꿔 넣어도 맞다.

    Args:
        answers (Mapping[str, str]): 빈칸 id → 입력. 빠진 칸은 빈 입력으로 본다.
        keys (Mapping[str, AnswerKey]): 빈칸 id → 정답 정보. 세트의 모든 칸이다.

    Returns:
        Grading: 칸마다 판정과 점수. 칸이 하나도 없으면 점수는 0.0이다.

    Examples:
        >>> g = grade({"a": "ribosome", "b": "mitochondria"},
        ...           {"a": AnswerKey("mitochondria", answer_group=1),
        ...            "b": AnswerKey("ribosome", answer_group=1)})
        >>> g.results, g.score
        ({'a': 'correct', 'b': 'correct'}, 1.0)
    """
    results: dict[str, Verdict] = {
        bid: judge(answers.get(bid, ""), key) for bid, key in keys.items()
    }
    for members in _groups(keys).values():
        for bid in _match_group(members, answers, keys):
            results[bid] = "correct"
    correct = sum(1 for v in results.values() if v == "correct")
    return Grading(results=results, score=correct / len(keys) if keys else 0.0)


def _groups(keys: Mapping[str, AnswerKey]) -> dict[str | int, list[str]]:
    groups: dict[str | int, list[str]] = {}
    for bid, key in keys.items():
        if key.answer_group is not None:
            groups.setdefault(key.answer_group, []).append(bid)
    return groups


def _match_group(
    members: list[str], answers: Mapping[str, str], keys: Mapping[str, AnswerKey]
) -> list[str]:
    """묶음 안에서 입력과 정답을 하나씩 짝지어, 맞는 짝이 가장 많아지게 한다(이분 매칭).

    정답 하나는 입력 하나에만 줄 수 있다. 그래서 같은 입력을 두 칸에 써도 한 칸만 맞는다.
    자기 칸에서 이미 맞은 칸을 먼저 짝지어, 그 칸이 다른 칸에 밀려 틀리지 않게 한다.

    Returns:
        list[str]: 짝을 찾아 ``correct`` 가 되는 칸 id.
    """
    # fits[칸] = 그 칸의 입력이 맞는 정답 칸들. 자기 칸이 있으면 맨 앞에 둔다.
    fits = {
        bid: [t for t in members if is_correct(answers.get(bid, ""), keys[t])] for bid in members
    }
    owner: dict[str, str] = {}  # 정답 칸 id → 그 정답을 차지한 입력 칸 id
    for bid in sorted(members, key=lambda b: b not in fits[b]):
        _augment(bid, fits, owner, set())
    return list(owner.values())


def _augment(bid: str, fits: dict[str, list[str]], owner: dict[str, str], seen: set[str]) -> bool:
    """bid에게 정답 하나를 준다. 이미 주인이 있으면 그 주인을 다른 정답으로 옮겨 본다."""
    for target in fits[bid]:
        if target in seen:
            continue
        seen.add(target)
        if target not in owner or _augment(owner[target], fits, owner, seen):
            owner[target] = bid
            return True
    return False
