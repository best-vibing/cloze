"""Prepare and build the page results consumed by the worker."""

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from PIL.Image import Image

from cloze.core.analyze import PRESETS, AskLLM
from cloze.core.analyze import analyze_document as analyze_tokens
from cloze.core.errors import LLMInvalidResponse, LLMPermanentError
from cloze.core.extract import extract_tokens
from cloze.core.extract.render import render_page
from cloze.core.filter import filter_candidates
from cloze.core.gate import GateResult, apply_gate
from cloze.core.models import Blank, BlankStyle, PageCandidates, PageTokens, Policy
from cloze.core.redact import encode_webp, redact
from cloze.core.select.gemini import AskSelectorLLM, RetryFeedback, select_gemini
from cloze.core.select.rule import build_doc_stats, select_rule


class UnreadablePDFError(ValueError):
    """Too little text was extracted to generate a blank set."""


@dataclass(frozen=True)
class PreparedPage:
    tokens: PageTokens
    image: Image
    has_text: bool


def prepare(pdf: bytes) -> list[PreparedPage]:
    """Extract text and images, leaving low-text pages available for rendering."""
    pages = extract_tokens(pdf)
    candidates = filter_candidates(pages, Policy(seed=0))
    readable = [
        _has_readable_text(page, options) for page, options in zip(pages, candidates, strict=True)
    ]
    # Compare as integers to include the exact 80% boundary.
    if 5 * readable.count(False) >= 4 * len(pages):
        raise UnreadablePDFError("unreadable_pdf")
    return [
        PreparedPage(page, render_page(pdf, page.page.page_no), has_text)
        for page, has_text in zip(pages, readable, strict=True)
    ]


def _has_readable_text(page: PageTokens, candidates: PageCandidates) -> bool:
    margins = {item.id for item in candidates.excluded if item.reason == "header_footer"}
    letters = (
        char
        for token in page.tokens
        if token.id not in margins
        for char in token.text
        if char.isalpha()
    )
    # Two letters also cover short Korean labels and one-letter mathematical variables.
    return next(letters, None) is not None and next(letters, None) is not None


type SelectionSource = Literal["gemini", "rule", "none"]


@dataclass(frozen=True)
class PageResult:
    page_no: int
    tokens: PageTokens
    blanks: list[Blank]
    image_webp: bytes
    source: SelectionSource
    attempts: int
    survival_ratio: float
    candidates: PageCandidates
    gate: GateResult | None


async def analyze_document(pages: Sequence[PreparedPage], ask_llm: AskLLM) -> dict[str, str]:
    return await analyze_tokens([page.tokens.tokens for page in pages], ask_llm)


def _resolved_policy(policy: Policy, analysis: Mapping[str, str] | None) -> Policy:
    if policy.preset != "auto":
        return policy
    value = analysis.get("preset") if analysis else None
    preset = value if value in PRESETS else "concept"
    values = policy.model_dump()
    values.update(preset=preset, include_numbers=policy.include_numbers or preset == "number")
    return Policy.model_validate(values)


async def _try_gemini(
    page: PageTokens,
    candidates: PageCandidates,
    policy: Policy,
    ask: AskSelectorLLM,
    avoid: set[int],
) -> tuple[GateResult | None, int, bool]:
    feedback: RetryFeedback | None = None
    previous: Mapping[str, object] | None = None
    attempts = 0

    async def record_response(
        prompt: str, schema: dict[str, object], /, *, seed: int, temperature: float
    ) -> Mapping[str, object]:
        nonlocal previous, attempts
        attempts += 1
        previous = await ask(prompt, schema, seed=seed, temperature=temperature)
        return previous

    for _ in range(2):
        previous = None
        try:
            selection = await select_gemini(
                page, candidates, policy, record_response, avoid=avoid, retry=feedback
            )
        except LLMPermanentError:
            return None, attempts, True
        except LLMInvalidResponse as error:
            reason = str(error)
        else:
            result = apply_gate(page, candidates, selection, policy)
            if result.passed:
                return result, attempts, False
            reasons = sorted({item.reason for item in result.rejected})
            reason = f"survival_ratio={result.quality['survival_ratio']:.3f}; {', '.join(reasons)}"
        feedback = RetryFeedback(previous_response=dict(previous or {}), reason=reason)
    return None, attempts, False


def _page_result(
    page: PreparedPage,
    candidates: PageCandidates,
    gate: GateResult | None,
    source: SelectionSource,
    attempts: int,
) -> PageResult:
    accepted = gate.blanks if gate is not None and gate.passed else []
    result = redact(page.image, [blank.bbox for blank in accepted])
    blanks = [
        Blank.model_validate(
            {
                **blank.model_dump(exclude={"style"}),
                "style": BlankStyle(
                    font_size_ratio=blank.style.font_size_ratio,
                    font_color=_contrasting_color(color),
                    bg_color=color,
                ),
            }
        )
        for blank, color in zip(accepted, result.colors, strict=True)
    ]
    return PageResult(
        page.tokens.page.page_no,
        page.tokens,
        blanks,
        encode_webp(result.image),
        source if blanks else "none",
        attempts,
        gate.quality["survival_ratio"] if gate is not None else 0.0,
        candidates,
        gate,
    )


def _contrasting_color(background: str) -> str:
    red, green, blue = (int(background[index : index + 2], 16) for index in (1, 3, 5))
    return "#1a1a1a" if 299 * red + 587 * green + 114 * blue >= 128_000 else "#ffffff"


async def build(
    pages: Sequence[PreparedPage],
    policy: Policy,
    ask_llm_for: Callable[[Literal["select"], int], AskSelectorLLM] | None,
    *,
    skip_pages: set[int],
    analysis: Mapping[str, str] | None,
    avoid_ids: dict[int, set[int]],
) -> AsyncIterator[PageResult]:
    """Yield each finished page. Transient failures are left to the worker."""
    applied = _resolved_policy(policy, analysis)
    tokens = [page.tokens for page in pages]
    candidates = filter_candidates(tokens, applied)
    stats = build_doc_stats(tokens, candidates)
    permanent_failure = False
    for page, options in zip(pages, candidates, strict=True):
        number = page.tokens.page.page_no
        if number in skip_pages:
            continue
        if not page.has_text:
            yield _page_result(page, options, None, "none", 0)
            continue
        avoid = avoid_ids.get(number, set())
        gate, attempts = None, 0
        if ask_llm_for is not None and not permanent_failure:
            gate, attempts, permanent_failure = await _try_gemini(
                page.tokens, options, applied, ask_llm_for("select", number), avoid
            )
        if gate is not None:
            yield _page_result(page, options, gate, "gemini", attempts)
            continue
        selection = select_rule(page.tokens, options, applied, avoid, stats)
        gate = apply_gate(page.tokens, options, selection, applied)
        yield _page_result(page, options, gate, "rule", attempts)
