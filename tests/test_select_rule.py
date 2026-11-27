from pathlib import Path

import pytest

from cloze.core.extract import extract_tokens
from cloze.core.filter import filter_candidates
from cloze.core.models import PageCandidates, PageInfo, PageTokens, Policy, Selection, Token
from cloze.core.phrase import MAX_PHRASE_TOKENS
from cloze.core.redact import PAD_RATIO, blank_bbox
from cloze.core.select.rule import build_doc_stats, select_rule

SAMPLE = Path(__file__).parent / "fixtures" / "sample.pdf"


def _token(id_: int, text: str, line_id: int = 0, size: float = 12.0, font: str = "Reg") -> Token:
    bbox = (0.1, 0.1, 0.2, 0.2)
    return Token(id=id_, text=text, bbox=bbox, line_id=line_id, font_size=size, fontname=font)


def _plain(count: int) -> list[Token]:
    """서로 다른 단어를 줄마다 하나씩 둔 동점 토큰들."""
    return [_token(i, f"word{'x' * i}", line_id=i) for i in range(count)]


def _select(
    tokens: list[Token],
    density: float = 100.0,
    seed: int = 1,
    avoid: frozenset[int] = frozenset(),
) -> list[int]:
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=tokens)
    candidates = PageCandidates(page_no=1, candidate_ids=[t.id for t in tokens], excluded=[])
    stats = build_doc_stats([page], [candidates])
    policy = Policy(seed=seed, density=density)
    selection = select_rule(page, candidates, policy, set(avoid), stats)
    assert selection.source == "rule"
    return _ids(selection)


def _ids(selection: Selection) -> list[int]:
    assert all(len(span) == 1 for span in selection.spans)
    assert len(selection.slots) == len(selection.spans)
    return [span[0] for span in selection.spans]


def test_count_follows_density_formula() -> None:
    assert len(_select(_plain(40), density=5.0)) == 2
    assert len(_select(_plain(10), density=5.0)) == 0  # round(0.5) == 0
    assert len(_select(_plain(30), density=10.0)) == 3


def test_bold_and_large_words_are_selected_first() -> None:
    tokens = _plain(20)
    tokens[7] = _token(7, "Momentum", line_id=7, font="Helvetica-Bold")
    tokens[13] = _token(13, "Gradient", line_id=13, size=24.0)
    assert _select(tokens, density=10.0) == [7, 13]


def test_at_most_two_per_line() -> None:
    tokens = [_token(i, f"word{'x' * i}", line_id=0, size=24.0) for i in range(5)]
    tokens += [_token(i, f"body{'y' * i}", line_id=i) for i in range(5, 10)]
    selected = _select(tokens, density=40.0)
    assert len(selected) == 4
    assert sum(1 for id_ in selected if id_ < 5) == 2


def test_avoid_is_respected_while_alternatives_exist() -> None:
    selected = _select(_plain(20), density=10.0, avoid=frozenset(range(10)))
    assert len(selected) == 2
    assert all(id_ >= 10 for id_ in selected)


def test_avoid_is_reused_only_when_short() -> None:
    avoid = frozenset({0, 1, 2})
    assert _select(_plain(4), density=100.0, avoid=avoid) == [0, 1, 2, 3]
    selected = _select(_plain(4), density=50.0, avoid=avoid)
    assert len(selected) == 2
    assert 3 in selected


def test_same_normalized_word_is_selected_once() -> None:
    tokens = [_token(0, "역전파는", 0), _token(1, "역전파를", 1), _token(2, "신경망", 2)]
    selected = _select(tokens)
    assert len(selected) == 2
    assert 2 in selected


def test_same_seed_is_repeatable_and_other_seed_changes_ties() -> None:
    tokens = _plain(40)
    first = _select(tokens, density=10.0, seed=1)
    assert first == _select(tokens, density=10.0, seed=1)
    assert first != _select(tokens, density=10.0, seed=2)


def test_empty_page_gives_empty_selection() -> None:
    assert _select([]) == []


def test_auto_preset_behaves_like_concept() -> None:
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=_plain(20))
    candidates = PageCandidates(page_no=1, candidate_ids=list(range(20)), excluded=[])
    stats = build_doc_stats([page], [candidates])
    auto = select_rule(page, candidates, Policy(seed=3, preset="auto"), set(), stats)
    concept = select_rule(page, candidates, Policy(seed=3, preset="concept"), set(), stats)
    assert auto == concept


def test_fixture_selects_headings_and_bold_first() -> None:
    pages = extract_tokens(SAMPLE.read_bytes())
    policy = Policy(seed=1, preset="concept")
    candidates = filter_candidates(pages, policy)
    stats = build_doc_stats(pages, candidates)
    selection = select_rule(pages[0], candidates[0], policy, set(), stats)
    words = {token.id: token.text for token in pages[0].tokens}
    texts = {" ".join(words[id_] for id_ in span) for span in selection.spans}
    # 제목 줄의 굵고 큰 두 단어는 구 하나로 묶인다.
    assert {"Gradient Descent", "Momentum"} <= texts
    assert not texts & {"Cloze", "Sample", "Handbook", "the", "over-"}


def _word(id_: int, text: str, line_id: int = 0, size: float = 12.0, bold: bool = True) -> Token:
    """줄 안에서 id 순서대로 오른쪽에 놓이는 토큰. 기본은 굵은 글씨다."""
    x0, y0 = 0.1 + (id_ % 10) * 0.1, 0.1 + line_id * 0.05
    font = "Helvetica-Bold" if bold else "Helvetica"
    bbox = (x0, y0, x0 + 0.08, y0 + 0.02)
    return Token(id=id_, text=text, bbox=bbox, line_id=line_id, font_size=size, fontname=font)


def _with_filler(tokens: list[Token], total: int = 20) -> list[Token]:
    """서로 다른 평범한 단어를 줄마다 하나씩 더해 페이지 단어 수를 맞춘다."""
    start = len(tokens)
    filler = [_token(start + i, f"filler{'z' * i}", line_id=100 + i) for i in range(total - start)]
    return [*tokens, *filler]


def _spans(
    tokens: list[Token],
    density: float = 10.0,
    seed: int = 1,
    avoid: frozenset[int] = frozenset(),
    skip: frozenset[int] = frozenset(),
) -> list[list[int]]:
    """skip에 든 id는 후보에서 뺀다(불용어 등)."""
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=tokens)
    ids = [t.id for t in tokens if t.id not in skip]
    candidates = PageCandidates(page_no=1, candidate_ids=ids, excluded=[])
    stats = build_doc_stats([page], [candidates])
    selection = select_rule(page, candidates, Policy(seed=seed, density=density), set(avoid), stats)
    assert len(selection.slots) == len(selection.spans)
    return selection.spans


def test_adjacent_bold_candidates_on_same_line_form_one_phrase() -> None:
    tokens = _with_filler([_word(0, "oxidative"), _word(1, "phosphorylation")])
    assert [0, 1] in _spans(tokens)


def test_large_regular_words_also_form_a_phrase() -> None:
    big = [_word(0, "Tie", size=24, bold=False), _word(1, "Breaking", size=24, bold=False)]
    tokens = _with_filler(big)
    assert [0, 1] in _spans(tokens)


@pytest.mark.parametrize(
    "pair",
    [
        [_word(0, "oxidative", line_id=0), _word(1, "phosphorylation", line_id=1)],  # 다른 줄
        [_word(0, "oxidative"), _word(2, "phosphorylation")],  # id가 끊김
        [_word(0, "oxidative", bold=False), _word(1, "phosphorylation", bold=False)],  # 강조 아님
        [_word(0, "oxidative"), _word(1, "phosphorylation", bold=False)],  # 스타일이 다름
        [_word(0, "ATP,"), _word(1, "NADH")],  # 쉼표에서 끊김
        [_word(0, "ATP"), _word(1, "(NADH)")],  # 여는 괄호에서 끊김
        [_word(0, "데이터를"), _word(1, "학습")],  # 가운데 어절에 조사
        [_word(0, "확률"), _word(1, "분포를")],  # 끝 어절에 조사(#130)
        [_word(0, "딥러닝"), _word(1, "모델이며")],  # 끝 어절에 조사(#130)
        [_word(0, "모델"), _word(1, "학습한다")],  # 끝 어절에 어미(#130)
        [_word(0, "사용하여"), _word(1, "학습")],  # 가운데 어절에 어미(#130)
    ],
)
def test_tokens_that_must_not_join_stay_separate(pair: list[Token]) -> None:
    spans = _spans(_with_filler(pair))
    assert all(len(span) == 1 for span in spans)


def test_non_candidate_between_breaks_the_phrase() -> None:
    tokens = _with_filler([_word(0, "rate"), _word(1, "of"), _word(2, "decay")])
    spans = _spans(tokens, skip=frozenset({1}))
    assert all(len(span) == 1 for span in spans)
    assert [0] in spans and [2] in spans


def test_korean_phrase_without_particle_joins() -> None:
    tokens = _with_filler([_word(0, "확률"), _word(1, "분포")])
    assert [0, 1] in _spans(tokens)


def test_phrase_is_at_most_max_tokens() -> None:
    longest = [_word(i, f"term{'x' * i}") for i in range(MAX_PHRASE_TOKENS)]
    assert list(range(MAX_PHRASE_TOKENS)) in _spans(_with_filler(longest))
    too_long = [_word(i, f"term{'x' * i}") for i in range(MAX_PHRASE_TOKENS + 1)]
    assert all(len(span) == 1 for span in _spans(_with_filler(too_long), density=40.0))


def test_phrase_counts_as_one_blank() -> None:
    tokens = _with_filler([_word(0, "oxidative"), _word(1, "phosphorylation")])
    assert _spans(tokens, density=5.0) == [[0, 1]]  # 목표 1칸


def test_word_inside_chosen_phrase_is_not_blanked_again() -> None:
    phrase = [_word(0, "Gradient", size=24), _word(1, "Descent", size=24)]
    tokens = _with_filler([*phrase, _word(2, "gradient", line_id=5)])
    spans = _spans(tokens, density=15.0)
    assert [0, 1] in spans
    assert [2] not in spans


def test_phrase_touching_avoid_is_used_only_when_short() -> None:
    phrase = [_word(0, "Gradient", size=24), _word(1, "Descent", size=24)]
    tokens = _with_filler([*phrase, _word(2, "Momentum", line_id=5)])
    assert _spans(tokens, density=5.0) == [[0, 1]]
    assert _spans(tokens, density=5.0, avoid=frozenset({1})) == [[2]]


def test_same_seed_gives_same_phrases() -> None:
    tokens = [_word(i, f"term{'x' * i}", line_id=i // 2) for i in range(20)]
    first = _spans(tokens, density=20.0, seed=7)
    assert first == _spans(tokens, density=20.0, seed=7)
    assert all(len(span) == 2 for span in first)


def test_phrase_bbox_is_union_on_one_line() -> None:
    tokens = [_word(0, "oxidative"), _word(1, "phosphorylation")]
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=tokens)
    single = blank_bbox(page, [0])
    box = blank_bbox(page, [0, 1])
    pad_x = 12.0 * PAD_RATIO / 600
    assert box[0] == pytest.approx(tokens[0].bbox[0] - pad_x)
    assert box[2] == pytest.approx(tokens[1].bbox[2] + pad_x)
    assert (box[1], box[3]) == (single[1], single[3])  # 높이는 한 줄 그대로
