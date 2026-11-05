from collections import Counter

import pytest

from cloze.core.models import Token
from cloze.core.select.rule import (
    DocStats,
    bold_signal,
    frequency_signal,
    heading_signal,
    score_token,
    size_signal,
)

EMPTY_STATS = DocStats(frequency=Counter(), heading_words=frozenset())


def _token(
    id_: int = 0,
    text: str = "gradient",
    line_id: int = 0,
    font_size: float = 12.0,
    fontname: str = "Helvetica",
) -> Token:
    return Token(
        id=id_,
        text=text,
        bbox=(0.1, 0.1, 0.2, 0.2),
        line_id=line_id,
        font_size=font_size,
        fontname=fontname,
    )


@pytest.mark.parametrize(
    ("font_size", "expected"),
    [(6.0, 0.0), (12.0, 0.0), (18.0, 0.5), (24.0, 1.0), (48.0, 1.0)],
)
def test_size_signal_scales_with_ratio_to_median(font_size: float, expected: float) -> None:
    assert size_signal(_token(font_size=font_size), page_median=12.0) == expected


@pytest.mark.parametrize(
    ("fontname", "expected"),
    [
        ("Helvetica-Bold", 1.0),
        ("ABCDEF+NanumGothicBold", 1.0),
        ("Arial-Black", 1.0),
        ("Roboto-Heavy", 1.0),
        ("Helvetica", 0.0),
    ],
)
def test_bold_signal_reads_fontname(fontname: str, expected: float) -> None:
    assert bold_signal(_token(fontname=fontname)) == expected


def test_frequency_signal_uses_normalized_words() -> None:
    stats = DocStats(
        frequency=Counter({"역전파": 4, "신경망": 2, "학습": 1}), heading_words=frozenset()
    )
    assert frequency_signal(_token(text="역전파는"), stats) == 1.0
    assert frequency_signal(_token(text="신경망이"), stats) == pytest.approx(0.5)
    assert frequency_signal(_token(text="학습"), stats) == 0.0
    assert frequency_signal(_token(text="없는단어"), stats) == 0.0


def test_frequency_signal_is_zero_when_nothing_repeats() -> None:
    stats = DocStats(frequency=Counter({"gradient": 1}), heading_words=frozenset())
    assert frequency_signal(_token(), stats) == 0.0


def test_heading_signal_matches_body_occurrence_of_heading_word() -> None:
    stats = DocStats(frequency=Counter(), heading_words=frozenset({"gradient"}))
    assert heading_signal(_token(text="Gradient,"), stats) == 1.0
    assert heading_signal(_token(text="momentum"), stats) == 0.0


def test_score_prefers_bold_and_large_over_plain() -> None:
    plain = score_token(_token(), 12.0, EMPTY_STATS)
    bold = score_token(_token(fontname="Helvetica-Bold"), 12.0, EMPTY_STATS)
    large = score_token(_token(font_size=24.0), 12.0, EMPTY_STATS)
    assert plain == 0.0
    assert plain < bold < large
