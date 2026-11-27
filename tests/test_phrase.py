"""cloze.core.phrase: 구 빈칸 모양 규칙(#47)."""

import pytest

from cloze.core.models import Token
from cloze.core.phrase import MAX_PHRASE_TOKENS, PhraseProblem, is_phrase, phrase_problem


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
