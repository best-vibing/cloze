"""페이지의 선택 결과를 검증하고 빈칸과 정답을 만든다."""

import math
import unicodedata
from collections import Counter
from dataclasses import dataclass
from itertools import zip_longest
from typing import Literal, TypedDict

from cloze.core.filter import normalize_ko, strip_punctuation, token_reason
from cloze.core.grade import normalize
from cloze.core.models import (
    BBox,
    ExcludeReason,
    GateBlank,
    GateStyle,
    PageCandidates,
    PageInfo,
    PageTokens,
    Policy,
    Selection,
    Slot,
    Token,
)
from cloze.core.select.rule import target_count

# [가설] 글자 크기에 곱해 사방에 더할 여백을 구한다.
PAD_RATIO = 0.15
MIN_SURVIVAL = 0.6
type RejectionReason = (
    ExcludeReason
    | Literal[
        "invalid_span",
        "slot_mismatch",
        "unknown_token",
        "not_candidate",
        "duplicate_token",
        "invalid_geometry",
        "overlap",
        "answer_exposed",
    ]
)


@dataclass(frozen=True)
class RejectedBlank:
    token_ids: list[int]
    reason: RejectionReason


class Quality(TypedDict):
    survival_ratio: float


@dataclass(frozen=True)
class GateResult:
    blanks: list[GateBlank]
    rejected: list[RejectedBlank]
    quality: Quality
    passed: bool


def apply_gate(
    page: PageTokens,
    candidates: PageCandidates,
    selection: Selection,
    policy: Policy,
) -> GateResult:
    """검증한 빈칸과 탈락 사유, 페이지 품질을 반환한다.

    Args:
        page: 원본 페이지 정보와 추출 토큰.
        candidates: 같은 페이지의 후보 및 제외 사유.
        selection: 선택기가 반환한 span과 슬롯.
        policy: 밀도와 숫자 허용 정책.

    Raises:
        ValueError: 후보 페이지가 다르거나 밀도가 유한하지 않은 경우.
    """
    if page.page.page_no != candidates.page_no or not math.isfinite(policy.density):
        raise ValueError("Expected matching pages and finite density")
    accepted, rejected = _validate_spans(page, candidates, selection, policy)
    blanks = _build_blanks(page, accepted, rejected)
    target = target_count(page, candidates, policy)
    ratio = len(blanks) / target if target > 0 else 1.0
    return GateResult(blanks, rejected, {"survival_ratio": ratio}, ratio >= MIN_SURVIVAL)


def _validate_spans(
    page: PageTokens,
    candidates: PageCandidates,
    selection: Selection,
    policy: Policy,
) -> tuple[list[tuple[Token, Slot]], list[RejectedBlank]]:
    tokens = {token.id: token for token in page.tokens}
    allowed = set(candidates.candidate_ids)
    accepted: list[tuple[Token, Slot]] = []
    rejected: list[RejectedBlank] = []
    seen: dict[int, BBox] = {}
    for span, slot in zip_longest(selection.spans, selection.slots):
        reason: RejectionReason | None = "slot_mismatch"
        if span is not None and slot is not None:
            reason = _span_reason(span, tokens, allowed, seen, page.page, policy)
            if reason is None:
                token = tokens[span[0]]
                accepted.append((token, slot))
                seen[token.id] = token.word_bbox or token.bbox
        if reason is not None:
            rejected.append(RejectedBlank(list(span or []), reason))
    return accepted, rejected


def _span_reason(
    span: list[int],
    tokens: dict[int, Token],
    allowed: set[int],
    seen: dict[int, BBox],
    page: PageInfo,
    policy: Policy,
) -> RejectionReason | None:
    if len(span) != 1 or type(span[0]) is not int:
        return "invalid_span"
    token = tokens.get(span[0])
    if token is None:
        return "unknown_token"
    if token.id in seen:
        return "duplicate_token"
    if reason := token_reason(token.text, policy.include_numbers):
        return reason
    if token.id not in allowed:
        return "not_candidate"
    return _geometry_reason(page, token, seen)


def _geometry_reason(page: PageInfo, token: Token, seen: dict[int, BBox]) -> RejectionReason | None:
    if not _valid_geometry(page, token):
        return "invalid_geometry"
    box = token.word_bbox or token.bbox
    if any(_overlaps(box, other) for other in seen.values()):
        return "overlap"
    return None


def _valid_geometry(page: PageInfo, token: Token) -> bool:
    if not all(
        math.isfinite(size) and size > 0
        for size in (
            page.width_pt,
            page.height_pt,
            token.font_size,
        )
    ):
        return False
    left, top, right, bottom = token.bbox
    x0, y0, x1, y1 = token.word_bbox or token.bbox
    return 0 <= left <= x0 < x1 <= right <= 1 and 0 <= top <= y0 < y1 <= bottom <= 1


def _overlaps(a: BBox, b: BBox) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _answer_key(text: str) -> str:
    return normalize_ko(normalize(text))


def _build_blanks(
    page: PageTokens,
    accepted: list[tuple[Token, Slot]],
    rejected: list[RejectedBlank],
) -> list[GateBlank]:
    occurrences = Counter(_answer_key(token.text) for token in page.tokens)
    covered = Counter(_answer_key(token.text) for token, _ in accepted)
    ordered = sorted(accepted, key=lambda item: (item[0].line_id, item[0].bbox[0]))
    blanks: list[GateBlank] = []
    groups: dict[str, int] = {}
    for token, slot in ordered:
        key = _answer_key(token.text)
        if covered[key] != occurrences[key]:
            rejected.append(RejectedBlank([token.id], "answer_exposed"))
            continue
        order = len(blanks)
        group = groups.setdefault(key, order) if covered[key] > 1 else None
        blanks.append(_build_blank(page.page, token, slot, order, group))
    return blanks


def _build_blank(
    page: PageInfo,
    token: Token,
    slot: Slot,
    order: int,
    group: int | None,
) -> GateBlank:
    answer = strip_punctuation(token.text)
    variants = _clean_variants(slot.variants, answer)
    hint = (slot.hint or "").strip()
    if not _plain_text(hint, 60) or any(
        _contains_answer(hint, value) for value in (answer, *variants)
    ):
        hint = ""
    return GateBlank(
        token_ids=[token.id],
        bbox=_span_bbox(page, [token], PAD_RATIO),
        type=slot.type,
        hint=hint or None,
        variants=variants,
        answer=answer,
        order=order,
        answer_group=group,
        style=GateStyle(font_size_ratio=token.font_size / page.width_pt),
    )


def _contains_answer(hint: str, answer: str) -> bool:
    return normalize(answer) in normalize(hint) or _answer_key(answer) in _answer_key(hint)


def _clean_variants(values: list[str], answer: str) -> list[str]:
    variants: list[str] = []
    seen = {_answer_key(answer)}
    for value in values:
        value = value.strip()
        key = _answer_key(value)
        if len(key) > 1 and key not in seen and _plain_text(value, 40):
            seen.add(key)
            variants.append(value)
            if len(variants) == 5:
                break
    return variants


def _plain_text(text: str, limit: int) -> bool:
    if not text or len(text) > limit or not text.isprintable():
        return False
    return not set("<>") & set(unicodedata.normalize("NFKC", text))


def blank_bbox(page: PageTokens, span: list[int], pad_ratio: float = PAD_RATIO) -> BBox:
    """묶인 토큰 bbox의 합집합에 패딩을 더하고 0~1로 자른다.
    앞뒤 문장 부호는 칠하지 않도록, 토큰에 word_bbox가 있으면 그것을 쓴다."""
    ids = set(span)
    tokens = [token for token in page.tokens if token.id in ids]
    if not tokens:
        raise ValueError(f"span {span}에 해당하는 토큰이 없다")
    return _span_bbox(page.page, tokens, pad_ratio)


def _span_bbox(page: PageInfo, tokens: list[Token], pad_ratio: float) -> BBox:
    inner = _union([token.word_bbox or token.bbox for token in tokens])
    outer = _union([token.bbox for token in tokens])
    # 줄 높이는 bbox 높이가 아니라 글자 크기로 잰다. 세로로 쓰인(회전) 글은 bbox 높이가 단어 길이다.
    # 패딩은 포인트로 정한 뒤 가로·세로 비율에 맞게 나눈다. 그래야 사방 두께가 같다.
    pad_pt = max(token.font_size for token in tokens) * pad_ratio
    pad_x, pad_y = pad_pt / page.width_pt, pad_pt / page.height_pt
    # 부호를 뗀 변(합집합의 바깥 변이 bbox와 다른 쪽)에는 패딩을 붙이지 않는다. 패딩(12pt에서
    # 1.8pt)이 글자와 부호 사이 여백(약 1pt)보다 커서 부호를 덮는다.
    # 변별로 비교하므로 회전 글도 같다.
    x0, y0, x1, y1 = (
        edge + pad if edge == full else edge
        for edge, full, pad in zip(inner, outer, (-pad_x, -pad_y, pad_x, pad_y), strict=True)
    )
    return (_unit(x0), _unit(y0), _unit(x1), _unit(y1))


def _union(boxes: list[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)
