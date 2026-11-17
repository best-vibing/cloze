from pathlib import Path

import pytest

from cloze.core.extract import extract_tokens
from cloze.core.filter import excluded_count, filter_candidates
from cloze.core.models import Excluded, PageCandidates, PageInfo, PageTokens, Policy, Token
from cloze.core.question_region import is_question_marker
from cloze.core.select.rule import build_doc_stats, select_rule

QUESTIONS = Path(__file__).parent / "fixtures" / "questions.pdf"
POLICY = Policy(seed=1)
UPPER_LIMIT = 0.5  # 두 장 모아찍기 fixture에서 위 슬라이드 바닥글의 top(0.46)보다 아래
type Regions = list[dict[str, set[str]]]


def _token(id_: int, text: str, top: float, line_id: int, size: float = 12.0) -> Token:
    bbox = (0.1 + 0.05 * (id_ % 10), top, 0.14 + 0.05 * (id_ % 10), top + 0.015)
    return Token(id=id_, text=text, bbox=bbox, line_id=line_id, font_size=size, fontname="R")


def _line_tokens(words: str, top: float, line_id: int, start: int) -> list[Token]:
    return [_token(start + i, word, top, line_id) for i, word in enumerate(words.split())]


@pytest.fixture(scope="module")
def regions() -> Regions:
    """쪽마다 question_region으로 빠진 토큰 글(upper/lower)과 후보로 남은 토큰 글(kept_*)."""
    pages = extract_tokens(QUESTIONS.read_bytes())
    out: Regions = []
    for page, result in zip(pages, filter_candidates(pages, POLICY), strict=True):
        questioned = {item.id for item in result.excluded if item.reason == "question_region"}
        kept = set(result.candidate_ids)
        upper = [t for t in page.tokens if t.bbox[1] < UPPER_LIMIT]
        lower = [t for t in page.tokens if t.bbox[1] >= UPPER_LIMIT]
        out.append(
            {
                "upper": {t.text for t in upper if t.id in questioned},
                "lower": {t.text for t in lower if t.id in questioned},
                "kept_upper": {t.text for t in upper if t.id in kept},
                "kept_lower": {t.text for t in lower if t.id in kept},
            }
        )
    return out


def test_upper_question_slide_is_excluded_and_lower_concept_is_kept(regions: Regions) -> None:
    page = regions[0]
    assert {"Example:", "Interval", "Solution:", "tensile", "confidence"} <= page["upper"]
    assert page["kept_upper"] == set()
    assert page["lower"] == set()
    assert {"Context", "window", "model", "tokens"} <= page["kept_lower"]


def test_lower_question_slide_is_excluded_and_upper_concept_is_kept(regions: Regions) -> None:
    page = regions[1]
    assert page["upper"] == set()
    assert {"Prompting", "model", "agent"} <= page["kept_upper"]
    assert {"Exercise", "prompt", "plan", "window"} <= page["lower"]
    assert page["kept_lower"] == set()


def test_both_question_slides_exclude_whole_page(regions: Regions) -> None:
    page = regions[2]
    assert {"Q1.", "agent", "Choose"} <= page["upper"]
    assert {"문제", "Describe", "context"} <= page["lower"]
    assert page["kept_upper"] == page["kept_lower"] == set()


def test_marker_word_inside_sentence_is_not_a_question(regions: Regions) -> None:
    page = regions[3]
    assert page["upper"] == page["lower"] == set()
    assert {"Agents", "agent", "example", "example,", "tests."} <= page["kept_upper"]


def test_marker_line_in_body_excludes_only_its_paragraph(regions: Regions) -> None:
    # 표시 줄이 슬라이드 첫 줄(제목 자리)이 아니면 개념 슬라이드일 수 있다. 그 문단만 뺀다.
    page = regions[4]
    assert page["upper"] == {"Example:", "learning", "rate", "controls", "the", "step", "size"}
    assert {"Gradient", "Descent", "minimizes", "loss", "Momentum", "convergence"} <= page[
        "kept_upper"
    ]
    assert page["lower"] == set()


@pytest.mark.parametrize(
    "line",
    [
        "Example:",
        "Examples",
        "Exercise 1",
        "Q1. What",
        "Q. What",
        "Q 1 What",
        "문제 1",
        "예제:",
        "연습문제",
    ],
)
def test_question_markers(line: str) -> None:
    assert is_question_marker(_line_tokens(line, 0.1, 0, 0))


@pytest.mark.parametrize(
    "line", ["example, the agent", "Example of REST", "for example, this", "Q&A", "문제는 데이터다"]
)
def test_non_markers(line: str) -> None:
    assert not is_question_marker(_line_tokens(line, 0.1, 0, 0))


def test_without_slide_boundary_only_the_following_paragraph_is_excluded() -> None:
    # 반복 머리글·바닥글이 없어 경계를 모른다.
    # 표시 줄과 바로 이어지는 줄(줄 높이의 1.5배 이내)만 뺀다.
    tokens = _line_tokens("Example: estimate the mean", 0.10, 0, 0)
    tokens += _line_tokens("using thirty samples", 0.12, 1, 10)
    tokens += _line_tokens("Context window", 0.30, 2, 20)  # 간격이 커서 다른 문단
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=tokens)
    result = filter_candidates([page], POLICY)[0]
    questioned = {item.id for item in result.excluded if item.reason == "question_region"}
    assert questioned == {0, 1, 2, 3, 10, 11, 12}
    assert result.candidate_ids == [20, 21]


def test_rule_selector_target_excludes_question_region() -> None:
    tokens = [_token(i, f"word{'x' * i}", 0.1 + 0.02 * i, i) for i in range(40)]
    page = PageTokens(page=PageInfo(page_no=1, width_pt=600, height_pt=800), tokens=tokens)
    excluded = [Excluded(id=i, reason="question_region") for i in range(20)]
    candidates = PageCandidates(page_no=1, candidate_ids=list(range(20, 40)), excluded=excluded)
    assert excluded_count(candidates, "question_region") == 20
    policy = Policy(seed=1, density=50)
    selection = select_rule(page, candidates, policy, set(), build_doc_stats([page], [candidates]))
    assert len(selection.spans) == 10  # (40 - 20) × 50% = 10. 전체 단어 수 기준이면 20이 된다.
