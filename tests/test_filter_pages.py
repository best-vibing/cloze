from pathlib import Path

from cloze.core.extract import extract_tokens
from cloze.core.filter import filter_candidates
from cloze.core.models import PageInfo, PageTokens, Policy, Token

SAMPLE = Path(__file__).parent / "fixtures" / "sample.pdf"
POLICY = Policy(seed=1)


def _page(page_no: int, lines: list[tuple[float, str]]) -> PageTokens:
    tokens = [
        Token(
            id=index,
            text=text,
            bbox=(0.1, top, 0.2, top + 0.01),
            line_id=line_id,
            font_size=12.0,
            fontname="Helvetica",
        )
        for line_id, (top, line) in enumerate(lines)
        for index, text in [(line_id * 100 + i, word) for i, word in enumerate(line.split())]
    ]
    return PageTokens(page=PageInfo(page_no=page_no, width_pt=600, height_pt=800), tokens=tokens)


def _reasons(page: PageTokens, policy: Policy = POLICY) -> dict[str, str]:
    result = filter_candidates([page], policy)[0]
    texts = {token.id: token.text for token in page.tokens}
    return {texts[item.id]: item.reason for item in result.excluded}


def test_candidates_and_exclusions_partition_tokens() -> None:
    page = _page(1, [(0.5, "gradient the x=1 over-")])
    result = filter_candidates([page], POLICY)[0]
    assert result.page_no == 1
    assert result.candidate_ids == [0]
    assert {item.id: item.reason for item in result.excluded} == {
        1: "stopword",
        2: "has_symbol",
        3: "trailing_hyphen",
    }


def test_include_numbers_controls_pure_numbers() -> None:
    page = _page(1, [(0.5, "2024 gradient")])
    assert _reasons(page) == {"2024": "number"}
    assert _reasons(page, Policy(seed=1, include_numbers=True)) == {}


def test_repeated_lines_in_similar_position_are_excluded() -> None:
    pages = [
        _page(1, [(0.02, "Course Handbook"), (0.5, "gradient"), (0.96, "Page 1")]),
        _page(2, [(0.03, "Course Handbook"), (0.5, "momentum"), (0.96, "Page 2")]),
        _page(3, [(0.02, "Course Handbook"), (0.5, "dropout"), (0.96, "Page 3")]),
    ]
    results = filter_candidates(pages, POLICY)
    assert [result.candidate_ids for result in results] == [[100], [100], [100]]
    assert all(item.reason == "header_footer" for result in results for item in result.excluded)


def test_same_text_in_different_position_is_kept() -> None:
    pages = [_page(1, [(0.1, "gradient")]), _page(2, [(0.5, "gradient")])]
    results = filter_candidates(pages, POLICY)
    assert [result.candidate_ids for result in results] == [[0], [0]]


def test_line_in_too_few_pages_is_kept() -> None:
    pages = [_page(no, [(0.5, "gradient" if no < 3 else f"word{'x' * no}")]) for no in range(1, 6)]
    assert filter_candidates(pages, POLICY)[0].candidate_ids == [0]


def test_single_page_has_no_header_footer() -> None:
    assert _reasons(_page(1, [(0.02, "Course Handbook")])) == {}


def test_empty_page_gives_empty_result() -> None:
    page = _page(1, [])
    assert filter_candidates([page], POLICY)[0].model_dump() == {
        "page_no": 1,
        "candidate_ids": [],
        "excluded": [],
    }


def test_fixture_header_footer_are_excluded() -> None:
    pages = extract_tokens(SAMPLE.read_bytes())
    for page, result in zip(pages, filter_candidates(pages, POLICY), strict=True):
        reasons = {item.id: item.reason for item in result.excluded}
        last_line = page.tokens[-1].line_id
        frame = [token for token in page.tokens if token.line_id in {0, last_line}]
        assert [token.text for token in frame[:3]] == ["Cloze", "Sample", "Handbook"]
        assert [token.text for token in frame[3:]][0] == "Page"
        assert {reasons[token.id] for token in frame} == {"header_footer"}
