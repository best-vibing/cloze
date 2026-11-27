"""cloze.core.phrase: 구 빈칸 모양 규칙(#47)과 구를 끊는 조건(#130)."""

import pytest

from cloze.core.models import Token
from cloze.core.phrase import (
    MAX_PHRASE_TOKENS,
    PhraseProblem,
    broken,
    is_phrase,
    phrase_problem,
)


def _tokens(*spec: tuple[int, int]) -> list[Token]:
    """(id, line_id) 목록으로 토큰을 만든다."""
    return [
        Token(id=i, text=f"w{i}", bbox=(0, 0, 0.1, 0.1), line_id=line, font_size=12, fontname="A")
        for i, line in spec
    ]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ([(5, 0)], None),
        ([(5, 0), (6, 0), (7, 0)], None),
        ([], "length"),
        ([(i, 0) for i in range(MAX_PHRASE_TOKENS + 1)], "length"),
        ([(5, 0), (7, 0)], "not_consecutive"),
        ([(6, 0), (5, 0)], "not_consecutive"),
        # 줄이 바뀌는 구. Lec3 p13의 "Bernoulli / trials."처럼 PDF에서 줄바꿈된 말
        ([(47, 5), (48, 6)], "crosses_line"),
        # id가 끊기고 줄도 바뀌면 id 쪽을 먼저 알린다
        ([(5, 0), (7, 1)], "not_consecutive"),
    ],
)
def test_phrase_problem(spec: list[tuple[int, int]], expected: PhraseProblem | None) -> None:
    tokens = _tokens(*spec)
    assert phrase_problem(tokens) == expected
    assert is_phrase(tokens) is (expected is None)


def _words(*texts: str) -> list[Token]:
    """한 줄에 이어진 토큰을 만든다."""
    return [
        Token(id=i, text=text, bbox=(0, 0, 0.1, 0.1), line_id=0, font_size=12, fontname="A")
        for i, text in enumerate(texts)
    ]


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (("딥러닝", "모델이며"), "phrase_break"),  # 끝 어절에 조사. #129 Lec2 p02에서 실제로 통과함
        (("모델", "학습한다"), "phrase_break"),  # 끝 어절에 어미
        (("Transformer", "모델입니다"), "phrase_break"),  # 끝 어절에 존댓말 끝
        (("데이터를", "학습"), "phrase_break"),  # 가운데 어절에 조사
        (("사용하여", "개인", "포트폴리오"), "phrase_break"),  # 가운데 어절에 어미
        (("확률과", "통계"), "phrase_break"),  # 반례: 조사를 품은 용어도 막는다(#130에서 정함)
        (("electron,", "transport"), "phrase_break"),  # 쉼표
        (("proton", "(gradient)"), "phrase_break"),  # 여는 부호
        (("확률", "분포"), None),
        (("Large", "Language", "Model"), None),
        (("Bernoulli", "trials."), None),  # 끝 토큰 뒤의 마침표는 끊지 않는다
        (("화면", "구성"), None),  # 짧은 어미(-면)는 목록에 없다
        (("광고", "효과"), None),  # 짧은 어미(-고)는 목록에 없다
        (("모델이며",), None),  # 한 단어 칸에는 적용하지 않는다
        (("학습한다",), None),
    ],
)
def test_phrase_break(texts: tuple[str, ...], expected: PhraseProblem | None) -> None:
    tokens = _words(*texts)
    assert phrase_problem(tokens) == expected
    assert broken(tokens) is (expected == "phrase_break")
