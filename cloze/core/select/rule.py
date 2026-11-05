"""규칙 기반 선택기. 신호별 점수를 가중합해 빈칸으로 만들 토큰을 고른다."""

import math
from collections import Counter
from dataclasses import dataclass

from cloze.core.filter import word_key
from cloze.core.models import Token

# [가설] 신호별 가중치. 합은 1이다. 실데이터로 보정한다(selection-and-quality-gate.md 2.5절).
WEIGHT_SIZE = 0.35
WEIGHT_BOLD = 0.25
WEIGHT_HEADING = 0.20
WEIGHT_FREQUENCY = 0.20
# [가설] 페이지 중앙값의 이 배수 이상이면 글자 크기 신호가 최대다.
SIZE_RATIO_CAP = 2.0
BOLD_MARKERS = ("BOLD", "BLACK", "HEAVY")


@dataclass(frozen=True)
class DocStats:
    frequency: Counter[str]
    heading_words: frozenset[str]


def size_signal(token: Token, page_median: float) -> float:
    ratio = token.font_size / page_median
    return min(max((ratio - 1.0) / (SIZE_RATIO_CAP - 1.0), 0.0), 1.0)


def bold_signal(token: Token) -> float:
    name = token.fontname.upper()
    return 1.0 if any(marker in name for marker in BOLD_MARKERS) else 0.0


def heading_signal(token: Token, stats: DocStats) -> float:
    return 1.0 if word_key(token.text) in stats.heading_words else 0.0


def frequency_signal(token: Token, stats: DocStats) -> float:
    """한 번만 나오면 0, 문서에서 가장 많이 나온 단어면 1이다."""
    count = stats.frequency[word_key(token.text)]
    highest = max(stats.frequency.values(), default=0)
    if count <= 1 or highest <= 1:
        return 0.0
    return math.log(count) / math.log(highest)


def score_token(token: Token, page_median: float, stats: DocStats) -> float:
    return (
        WEIGHT_SIZE * size_signal(token, page_median)
        + WEIGHT_BOLD * bold_signal(token)
        + WEIGHT_HEADING * heading_signal(token, stats)
        + WEIGHT_FREQUENCY * frequency_signal(token, stats)
    )
