"""대표 페이지 선정의 모델 독립적인 계약을 검증한다."""

from dataclasses import dataclass

import pytest

from cloze.core.analyze import (
    MAX_ANALYSIS_INPUT_CHARS,
    MIN_PAGE_TEXT_CHARS,
    extract_heading_lines,
    limit_analysis_input,
    select_representative_pages,
)


@pytest.mark.parametrize("count", range(8))
def test_short_documents_keep_all_eligible_pages(count: int) -> None:
    pages = list(range(count))
    assert select_representative_pages(pages, lambda _: "가" * MIN_PAGE_TEXT_CHARS) == pages


def test_sampling_keeps_front_and_spreads_across_remainder() -> None:
    pages = list(range(22))
    result = select_representative_pages(pages, lambda _: "가" * MIN_PAGE_TEXT_CHARS)
    assert result == [0, 1, 2, 6, 11, 16, 21]
    assert len(result) == len(set(result))
    assert pages == list(range(22))
    assert select_representative_pages(pages, lambda _: "가" * MIN_PAGE_TEXT_CHARS) == result


def test_empty_and_sparse_pages_are_excluded_before_sampling() -> None:
    texts = ["", " \n\t", "짧음", "가" * MIN_PAGE_TEXT_CHARS, "나" * MIN_PAGE_TEXT_CHARS]
    assert select_representative_pages(list(range(len(texts))), texts.__getitem__) == [3, 4]


def test_whitespace_does_not_make_a_page_sufficient() -> None:
    texts = [" " * 100, "가" * (MIN_PAGE_TEXT_CHARS - 1) + " \n" * 100]
    assert select_representative_pages(texts, str) == []


@pytest.mark.parametrize("count", [8, 9, 10, 30, 100])
def test_spread_has_seven_distinct_ordered_pages(count: int) -> None:
    result = select_representative_pages(list(range(count)), lambda _: "가" * MIN_PAGE_TEXT_CHARS)
    assert len(result) == 7
    assert result == sorted(set(result))
    assert result[:2] == [0, 1]
    assert result[-1] == count - 1


@dataclass
class _TokenStub:
    """PR #15의 읽기 필드를 제공하는 테스트 대역."""

    text: str
    line_id: int
    font_size: float | None


def test_heading_preserves_line_and_token_order() -> None:
    """줄 번호나 좌표 대신 추출 순서를 유지하는지 검증한다."""
    tokens = [
        _TokenStub("둘째", 9, 24),
        _TokenStub("작은글씨", 9, 12),
        _TokenStub("본문", 2, 10),
        _TokenStub("첫째", 1, 24),
        _TokenStub("부제", 9, None),
    ]
    original = list(tokens)
    assert extract_heading_lines(tokens) == ["둘째 작은글씨 부제", "첫째"]
    assert extract_heading_lines(tokens) == extract_heading_lines(tokens)
    assert tokens == original


@pytest.mark.parametrize("size", [None, 0, -1, float("nan"), float("inf"), -float("inf")])
def test_heading_ignores_missing_or_invalid_sizes(size: float | None) -> None:
    """유효한 크기가 없으면 제목을 추측하지 않는지 검증한다."""
    assert extract_heading_lines([_TokenStub("본문", 1, size)]) == []


def test_empty_heading_and_whitespace_are_safe() -> None:
    """빈 토큰과 큰 글씨 공백이 제목 선정에 영향을 주지 않는지 검증한다."""
    assert extract_heading_lines([]) == []
    tokens = [_TokenStub(" \t", 1, 99), _TokenStub("제목", 2, 20)]
    assert extract_heading_lines(tokens) == ["제목"]


def test_heading_line_ids_are_scoped_to_each_page() -> None:
    """페이지마다 반복되는 줄 번호가 다른 페이지와 섞이지 않는지 검증한다."""
    pages = [[_TokenStub("표지", 1, 30)], [_TokenStub("절 제목", 1, 20)]]
    assert [extract_heading_lines(page) for page in pages] == [["표지"], ["절 제목"]]


@pytest.mark.parametrize(
    "text", ["", "짧은 입력", "가" * MAX_ANALYSIS_INPUT_CHARS], ids=["empty", "short", "limit"]
)
def test_input_within_default_budget_is_preserved(text: str) -> None:
    """기본 문자 예산 이하인 입력은 그대로인지 검증한다."""
    assert limit_analysis_input(text) == text


def test_long_input_is_limited_by_characters() -> None:
    """한글과 이모지도 Python 문자 길이 기준으로 제한하는지 검증한다."""
    text = "지시문\n" + "가🙂ab" * MAX_ANALYSIS_INPUT_CHARS
    result = limit_analysis_input(text)
    assert len(result) == MAX_ANALYSIS_INPUT_CHARS
    assert result == text[:MAX_ANALYSIS_INPUT_CHARS]
    assert limit_analysis_input("가🙂ab", 2) == "가🙂"


def test_zero_and_negative_input_budgets() -> None:
    """0과 음수 예산의 명시된 동작을 검증한다."""
    assert limit_analysis_input("본문", 0) == ""
    with pytest.raises(ValueError, match="0 이상"):
        limit_analysis_input("본문", -1)


def test_boolean_budget_is_rejected() -> None:
    """정수의 하위 타입인 bool이 실수로 예산에 쓰이지 않도록 검증한다."""
    with pytest.raises(TypeError, match="정수"):
        limit_analysis_input("본문", True)
