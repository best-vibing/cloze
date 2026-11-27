import pytest

from cloze.core.gate import apply_gate, blank_bbox
from cloze.core.models import (
    Blank,
    Excluded,
    PageCandidates,
    PageInfo,
    PageTokens,
    Policy,
    Selection,
    Slot,
    Token,
)
from cloze.core.phrase import MAX_PHRASE_TOKENS
from cloze.core.redact import blank_bbox as legacy_blank_bbox
from cloze.core.select.rule import build_doc_stats, select_rule


def page_with(*words: str) -> PageTokens:
    tokens = [
        Token(
            id=20 - i,
            text=word,
            line_id=1,
            bbox=(i / 10, 0.2, i / 10 + 0.08, 0.3),
            font_size=10,
            fontname="Arial",
        )
        for i, word in enumerate(words)
    ]
    return PageTokens(page=PageInfo(page_no=1, width_pt=200, height_pt=100), tokens=tokens)


def selection(*ids: int) -> Selection:
    return Selection(spans=[[i] for i in ids], slots=[Slot() for _ in ids], source="gemini")


def candidates(page: PageTokens, excluded: list[Excluded] | None = None) -> PageCandidates:
    removed = excluded or []
    return PageCandidates(
        page_no=1,
        candidate_ids=[
            token.id for token in page.tokens if token.id not in {item.id for item in removed}
        ],
        excluded=removed,
    )


def test_reading_order_group_and_style() -> None:
    page = page_with("(세포는)", "세포가", "원리")
    page.tokens[0].word_bbox = (0.01, 0.2, 0.07, 0.3)
    page.tokens[2].line_id = 0
    result = apply_gate(page, candidates(page), selection(19, 20, 18), Policy(seed=1, density=100))
    assert result.passed and result.quality == {"survival_ratio": 1.0}
    assert [(b.answer, b.order, b.answer_group) for b in result.blanks] == [
        ("원리", 0, None),
        ("세포는", 1, 1),
        ("세포가", 2, 1),
    ]
    blank = result.blanks[1]
    assert blank.bbox == blank_bbox(page, [20]) == legacy_blank_bbox(page, [20])
    assert blank.bbox[::2] == (0.01, 0.07)
    assert blank.style.model_dump() == {"font_size_ratio": 0.05}
    colors = {"font_color": "#222222", "bg_color": "#ffffff"}
    final = Blank.model_validate(blank.model_dump() | {"style": blank.style.model_dump() | colors})
    assert final.answer == "세포는"


@pytest.mark.parametrize(
    ("word", "reason"),
    [
        ("bio-", "trailing_hyphen"),
        ("A3+", "has_symbol"),
        ("ab", "too_short"),
        ("핵", "too_short"),
        ("a" * 26, "too_long"),
        ("2026", "number"),
    ],
)
def test_invalid_token(word: str, reason: str) -> None:
    page = page_with(word)
    result = apply_gate(page, candidates(page), selection(20), Policy(seed=1, density=100))
    assert not result.blanks and not result.passed
    assert result.rejected[0].reason == reason


def test_format_geometry_and_candidates() -> None:
    page = page_with("alpha", "beta", "gamma", "delta", "epsilon")
    page.tokens[1].bbox = (0.01, 0.2, 0.05, 0.3)
    page.tokens[2].bbox = (0.3, 0.2, float("nan"), 0.3)
    chosen = selection(20, 20, 19, 18, 17, 99)
    chosen.spans.extend([[], [16, 18], [16]])
    chosen.slots.extend([Slot(), Slot()])
    options = candidates(page, [Excluded(id=17, reason="question_region")])
    result = apply_gate(page, options, chosen, Policy(seed=1, density=100))
    assert [item.reason for item in result.rejected] == [
        "duplicate_token",
        "overlap",
        "invalid_geometry",
        "not_candidate",
        "unknown_token",
        "invalid_span",
        "invalid_span",
        "slot_mismatch",
    ]
    assert [blank.token_ids for blank in result.blanks] == [[20]]


def test_exposed_answer_in_ineligible_token() -> None:
    page = page_with("세포는", "세포가", "원리")
    options = candidates(page).model_copy(update={"candidate_ids": [18, 20]})
    result = apply_gate(page, options, selection(20, 18), Policy(seed=1, density=100))
    assert [item.reason for item in result.rejected] == ["answer_exposed"]
    assert [blank.answer for blank in result.blanks] == ["원리"]


def test_completes_answer_group_without_inflating_survival() -> None:
    page = page_with("세포는", "세포가", "세포를")
    result = apply_gate(page, candidates(page), selection(20), Policy(seed=1, density=100))
    assert len(result.blanks) == 3
    assert {blank.answer_group for blank in result.blanks} == {0}
    assert result.quality["survival_ratio"] == pytest.approx(1 / 3)


def test_rejects_group_larger_than_three() -> None:
    page = page_with("세포는", "세포가", "세포를", "세포에")
    result = apply_gate(page, candidates(page), selection(20), Policy(seed=1, density=100))
    assert not result.blanks
    assert [item.reason for item in result.rejected] == ["answer_exposed"]


def test_does_not_partially_expand_group_with_overlapping_location() -> None:
    page = page_with("세포는", "세포가", "세포를", "원리")
    page.tokens[2].bbox = page.tokens[3].bbox
    result = apply_gate(page, candidates(page), selection(20, 17), Policy(seed=1, density=100))
    assert [blank.answer for blank in result.blanks] == ["원리"]
    assert [item.reason for item in result.rejected] == ["answer_exposed"]


@pytest.mark.parametrize(
    ("count", "ratio", "passed"), [(2, 0.4, False), (3, 0.6, True), (0, 0, False)]
)
def test_survival_with_question_region(count: int, ratio: float, passed: bool) -> None:
    page = page_with(
        "alpha", "beta", "gamma", "delta", "epsilon", "exercise", "question", "example"
    )
    excluded = [Excluded(id=i, reason="question_region") for i in (15, 14, 13)]
    result = apply_gate(
        page,
        candidates(page, excluded),
        selection(*range(20, 20 - count, -1)),
        Policy(seed=1, density=100),
    )
    assert result.quality["survival_ratio"] == ratio and result.passed == passed


@pytest.mark.parametrize("words", [(), ("exercises",)])
def test_zero_target(words: tuple[str, ...]) -> None:
    page = page_with(*words)
    excluded = [Excluded(id=t.id, reason="question_region") for t in page.tokens]
    result = apply_gate(page, candidates(page, excluded), selection(), Policy(seed=1, density=100))
    assert not result.blanks and not result.rejected and result.passed
    assert result.quality == {"survival_ratio": 1.0}


@pytest.mark.parametrize(
    ("hint", "kept"),
    [
        ("ATP is described here", None),
        ("Uses ＡＴＰ!", None),
        ("(MiTo-chondria)", None),
        ("<script>alert(1)</script>", None),
        ("x" * 61, None),
        ("Cell structure", "Cell structure"),
    ],
)
def test_clean_generated_text(hint: str, kept: str | None) -> None:
    page = page_with("Mitochondria")
    chosen = selection(20)
    chosen.slots[0] = Slot(
        hint=hint,
        variants=[
            "",
            "!!",
            "a",
            "Ｍitochondria",
            "x" * 41,
            "＜script＞",
            "a\u202eb",
            "ATP",
            "atp",
            "motors",
            "units",
            "bodies",
            "engines",
            "organelles",
        ],
    )
    result = apply_gate(page, candidates(page), chosen, Policy(seed=1, density=100))
    assert result.blanks[0].hint == kept
    assert result.blanks[0].variants == ["ATP", "motors", "units", "bodies", "engines"]


def line(*lines: tuple[str, ...]) -> PageTokens:
    """줄마다 단어를 왼쪽부터 놓고, id는 읽는 순서대로 0부터 붙인다."""
    tokens: list[Token] = []
    for line_id, words in enumerate(lines):
        top = 0.1 + line_id * 0.1
        for x, word in enumerate(words):
            tokens.append(
                Token(
                    id=len(tokens),
                    text=word,
                    line_id=line_id,
                    bbox=(0.05 + x * 0.12, top, 0.15 + x * 0.12, top + 0.05),
                    font_size=10,
                    fontname="Arial",
                )
            )
    return PageTokens(page=PageInfo(page_no=1, width_pt=200, height_pt=100), tokens=tokens)


def spans(*items: list[int]) -> Selection:
    return Selection(spans=list(items), slots=[Slot() for _ in items], source="rule")


def test_phrase_span_becomes_one_blank() -> None:
    page = line(("Gradient", "Descent,", "momentum"))
    result = apply_gate(page, candidates(page), spans([0, 1]), Policy(seed=1, density=100))
    assert not result.rejected
    [blank] = result.blanks
    assert blank.token_ids == [0, 1]
    assert blank.answer == "Gradient Descent"
    assert blank.bbox == blank_bbox(page, [0, 1])
    assert result.quality["survival_ratio"] == pytest.approx(1 / 3)


@pytest.mark.parametrize(
    "span",
    [
        [0, 2],  # 이어지지 않음
        [1, 0],  # 거꾸로
        [2, 3],  # 다른 줄
        list(range(MAX_PHRASE_TOKENS + 1)),  # 너무 김
    ],
)
def test_phrase_span_must_be_contiguous_on_one_line(span: list[int]) -> None:
    page = line(("alpha", "beta", "gamma"), ("delta", "omega"))
    if len(span) > 3:
        page = line(tuple(f"word{'x' * i}" for i in range(len(span))))
    result = apply_gate(page, candidates(page), spans(span), Policy(seed=1, density=100))
    assert [item.reason for item in result.rejected] == ["invalid_span"]
    assert not result.blanks


@pytest.mark.parametrize(
    "words",
    [
        ("딥러닝", "모델이며"),  # 끝 어절에 조사(#130)
        ("모델", "학습한다"),  # 끝 어절에 어미
        ("데이터를", "학습"),  # 가운데 어절에 조사
        ("확률과", "통계"),  # 조사를 품은 용어도 막는다
        ("electron,", "transport"),  # 쉼표. 정답은 부호를 떼도 상자가 쉼표를 덮는다
        ("proton", "(gradient)"),  # 여는 부호
    ],
)
def test_phrase_that_is_a_sentence_fragment_is_rejected(words: tuple[str, str]) -> None:
    page = line(words)
    result = apply_gate(page, candidates(page), spans([0, 1]), Policy(seed=1, density=100))
    assert [item.reason for item in result.rejected] == ["phrase_break"]
    assert not result.blanks


def test_phrase_with_non_candidate_token_is_rejected() -> None:
    page = line(("gradient", "descent"))
    options = candidates(page).model_copy(update={"candidate_ids": [0]})
    result = apply_gate(page, options, spans([0, 1]), Policy(seed=1, density=100))
    assert [item.reason for item in result.rejected] == ["not_candidate"]


def test_phrase_exposed_only_by_the_same_phrase() -> None:
    # 구 안의 단어가 따로 나와도 구 정답이 드러난 것은 아니다.
    page = line(("gradient", "descent"), ("gradient", "boosting"))
    result = apply_gate(page, candidates(page), spans([0, 1]), Policy(seed=1, density=100))
    assert [blank.answer for blank in result.blanks] == ["gradient descent"]

    # 같은 구가 후보가 아닌 곳에 또 있으면 노출로 버린다.
    page = line(("gradient", "descent"), ("Gradient", "Descent"))
    options = candidates(page).model_copy(update={"candidate_ids": [0, 1]})
    result = apply_gate(page, options, spans([0, 1]), Policy(seed=1, density=100))
    assert [item.reason for item in result.rejected] == ["answer_exposed"]
    assert not result.blanks


def test_repeated_phrase_is_masked_as_a_group() -> None:
    page = line(("gradient", "descent"), ("Gradient", "Descent"))
    result = apply_gate(page, candidates(page), spans([0, 1]), Policy(seed=1, density=100))
    assert [blank.token_ids for blank in result.blanks] == [[0, 1], [2, 3]]
    assert {blank.answer_group for blank in result.blanks} == {0}
    assert result.quality["survival_ratio"] == pytest.approx(1 / 4)


def test_rule_selector_phrase_survives_the_gate() -> None:
    bold = [
        Token(
            id=i,
            text=word,
            line_id=0,
            bbox=(0.1 + i * 0.2, 0.1, 0.28 + i * 0.2, 0.14),
            font_size=24,
            fontname="Helvetica-Bold",
        )
        for i, word in enumerate(("Gradient", "Descent"))
    ]
    filler = [
        Token(
            id=2 + i,
            text=f"filler{'z' * i}",
            line_id=1 + i,
            bbox=(0.1, 0.2 + i * 0.03, 0.3, 0.22 + i * 0.03),
            font_size=12,
            fontname="Helvetica",
        )
        for i in range(18)
    ]
    info = PageInfo(page_no=1, width_pt=600, height_pt=800)
    page = PageTokens(page=info, tokens=[*bold, *filler])
    options = candidates(page)
    policy = Policy(seed=1, density=10)
    selection = select_rule(page, options, policy, set(), build_doc_stats([page], [options]))
    assert [0, 1] in selection.spans
    result = apply_gate(page, options, selection, policy)
    assert "invalid_span" not in {item.reason for item in result.rejected}
    assert "Gradient Descent" in {blank.answer for blank in result.blanks}
