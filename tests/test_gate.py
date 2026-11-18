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
from cloze.core.redact import blank_bbox as legacy_blank_bbox


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
    chosen.spans.extend([[], [16, 17], [16]])
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


def test_exposed_answer_in_unselected_token() -> None:
    page = page_with("세포는", "세포가", "원리")
    result = apply_gate(page, candidates(page), selection(20, 18), Policy(seed=1, density=100))
    assert [item.reason for item in result.rejected] == ["answer_exposed"]
    assert [blank.answer for blank in result.blanks] == ["원리"]


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
