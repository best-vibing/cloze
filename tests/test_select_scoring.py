from collections import Counter

import pytest

from cloze.core.models import PageCandidates, PageInfo, PageTokens, Token
from cloze.core.select.rule import (
    DocStats,
    bold_signal,
    build_doc_stats,
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


def _doc(tokens: list[Token]) -> tuple[list[PageTokens], list[PageCandidates]]:
    info = PageInfo(page_no=1, width_pt=600, height_pt=800)
    ids = [token.id for token in tokens]
    return (
        [PageTokens(page=info, tokens=tokens)],
        [PageCandidates(page_no=1, candidate_ids=ids, excluded=[])],
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


def test_build_doc_stats_counts_only_candidates() -> None:
    pages, candidates = _doc([_token(0, "역전파는"), _token(1, "역전파를"), _token(2, "신경망")])
    candidates[0].candidate_ids.remove(2)
    stats = build_doc_stats(pages, candidates)
    assert stats.frequency == Counter({"역전파": 2})


def test_heading_words_come_from_largest_lines() -> None:
    body = [_token(i, f"word{'x' * i}", line_id=i) for i in range(1, 10)]
    title = _token(0, "Gradient", line_id=0, font_size=24.0)
    pages, candidates = _doc([title, *body])
    assert build_doc_stats(pages, candidates).heading_words == frozenset({"gradient"})


def test_uniform_font_has_no_heading_words() -> None:
    tokens = [_token(i, f"word{'x' * i}", line_id=i) for i in range(5)]
    pages, candidates = _doc(tokens)
    assert build_doc_stats(pages, candidates).heading_words == frozenset()


def test_empty_document_stats() -> None:
    stats = build_doc_stats(*_doc([]))
    assert stats.frequency == Counter()
    assert stats.heading_words == frozenset()
