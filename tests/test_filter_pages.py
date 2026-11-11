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


TWOUP = Path(__file__).parent / "fixtures" / "twoup.pdf"
FOOTER = {"Vibe": "header_footer", "coding": "header_footer"}
FOOTER |= {"Kwon,": "header_footer", "Bokyung": "header_footer"}


def _footer_pages(*footers: str, cover: list[tuple[float, str]] | None = None) -> list[PageTokens]:
    """3쪽짜리 문서. 쪽마다 본문 한 줄과 바닥글 줄(top 0.95, 0.85, …)이 있다. cover는 1쪽에만."""
    pages = []
    for no in range(1, 4):
        lines = [*(cover or [] if no == 1 else []), (0.5, f"body{'x' * no}")]
        lines += [(0.95 - 0.1 * index, footer) for index, footer in enumerate(footers)]
        pages.append(_page(no, lines))
    return pages


def _line_reasons(pages: list[PageTokens]) -> dict[int, dict[str, dict[str, str]]]:
    """쪽 번호 → 줄 글 → {토큰: 제외 사유}. 후보로 남은 토큰은 들어 있지 않다."""
    results = filter_candidates(pages, POLICY)
    out: dict[int, dict[str, dict[str, str]]] = {}
    for page, result in zip(pages, results, strict=True):
        reasons = {item.id: item.reason for item in result.excluded}
        lines: dict[int, list[Token]] = {}
        for token in page.tokens:
            lines.setdefault(token.line_id, []).append(token)
        out[page.page.page_no] = {
            " ".join(t.text for t in tokens): {
                t.text: reasons[t.id] for t in tokens if t.id in reasons
            }
            for tokens in lines.values()
        }
    return out


def test_every_repeated_position_is_excluded() -> None:
    # 두 장 모아찍기 PDF는 바닥글이 한 쪽에 두 번(top 0.95, 0.85) 나온다. 둘 다 빠져야 한다.
    pages = _footer_pages("Vibe coding", "Vibe coding")
    for result in filter_candidates(pages, POLICY):
        assert result.candidate_ids == [0]
        assert [item.reason for item in result.excluded] == ["header_footer"] * 4


def test_footer_phrase_elsewhere_is_excluded_ignoring_case() -> None:
    pages = _footer_pages("Vibe coding", cover=[(0.1, "Vibe Coding"), (0.2, "AI coding tools")])
    assert "Vibe" not in _reasons(pages[0])  # 한 쪽만 보면 반복도, 바닥글 구절도 없다
    reasons = _line_reasons(pages)[1]
    assert reasons["Vibe Coding"] == {"Vibe": "footer_phrase", "Coding": "footer_phrase"}
    assert reasons["AI coding tools"] == {"AI": "too_short"}  # coding, tools는 후보로 남는다


def test_single_word_footer_does_not_match_elsewhere() -> None:
    pages = _footer_pages("Coding", cover=[(0.1, "Coding tools"), (0.2, "Vibe Coding")])
    reasons = _line_reasons(pages)[1]
    assert reasons["Coding tools"] == {}
    assert reasons["Vibe Coding"] == {}


def test_footer_line_is_split_at_separator() -> None:
    cover = [(0.1, "Kwon, Bokyung"), (0.2, "coding | Kwon,")]
    reasons = _line_reasons(_footer_pages("Vibe coding | Kwon, Bokyung", cover=cover))[1]
    assert reasons["Kwon, Bokyung"] == {"Kwon,": "footer_phrase", "Bokyung": "footer_phrase"}
    assert reasons["coding | Kwon,"] == {"|": "has_symbol"}  # 구절 경계를 넘으면 맞지 않는다


def _placed(id_: int, text: str, x0: float, top: float, line_id: int) -> Token:
    bbox = (x0, top, x0 + 0.05, top + 0.01)
    return Token(id=id_, text=text, bbox=bbox, line_id=line_id, font_size=9, fontname="R")


def test_footer_line_is_split_at_large_gap() -> None:
    # "Kwon, Bokyung"은 "coding"에서 0.44 × 600pt 떨어져 있다. 글자 크기 9pt의 1.5배보다 훨씬 멀다.
    footer = [_placed(0, "Vibe", 0.1, 0.95, 0), _placed(1, "coding", 0.16, 0.95, 0)]
    footer += [_placed(2, "Kwon,", 0.6, 0.95, 0), _placed(3, "Bokyung", 0.66, 0.95, 0)]
    cover = [_placed(4, "coding", 0.1, 0.1, 1), _placed(5, "Kwon,", 0.16, 0.1, 1)]
    cover += [_placed(6, "Vibe", 0.1, 0.2, 2), _placed(7, "Coding", 0.16, 0.2, 2)]
    first = PageInfo(page_no=1, width_pt=600, height_pt=800)
    second = PageInfo(page_no=2, width_pt=600, height_pt=800)
    pages = [
        PageTokens(page=first, tokens=[*footer, *cover]),
        PageTokens(page=second, tokens=footer),
    ]
    result = filter_candidates(pages, POLICY)[0]
    assert result.candidate_ids == [4, 5]
    assert {item.id: item.reason for item in result.excluded if item.id > 3} == {
        6: "footer_phrase",
        7: "footer_phrase",
    }


def test_twoup_fixture_footers_and_cover_phrases_are_excluded() -> None:
    pages = extract_tokens(TWOUP.read_bytes())
    reasons = _line_reasons(pages)
    for page in pages:
        footer_tops = sorted(t.bbox[1] for t in page.tokens if t.text == "Bokyung")[-2:]
        assert footer_tops[0] < 0.5 < footer_tops[1]  # 바닥글이 한 쪽에 두 번 나온다
        assert reasons[page.page.page_no]["Vibe coding Kwon, Bokyung"] == FOOTER
    assert reasons[1]["Vibe Coding"] == {"Vibe": "footer_phrase", "Coding": "footer_phrase"}
    assert reasons[1]["Kwon, Bokyung"] == {"Kwon,": "footer_phrase", "Bokyung": "footer_phrase"}
    assert reasons[1]["AI coding tools"] == {"AI": "too_short"}
    subtitle = reasons[1]["LLM, and generative AI coding tools"]
    assert subtitle == {"and": "stopword", "AI": "too_short"}
