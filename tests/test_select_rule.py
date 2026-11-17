from pathlib import Path

from cloze.core.extract import extract_tokens
from cloze.core.filter import filter_candidates
from cloze.core.models import PageCandidates, PageInfo, PageTokens, Policy, Selection, Token
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
    texts = {pages[0].tokens[span[0]].text for span in selection.spans}
    assert {"Gradient", "Descent", "Momentum"} <= texts
    assert not texts & {"Cloze", "Sample", "Handbook", "the", "over-"}
