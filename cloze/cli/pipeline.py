"""Run the page pipeline without requiring a worker or database."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from cloze.config import load_settings
from cloze.core.extract import EXTRACT_VERSION
from cloze.core.models import SCHEMA_VERSION, Policy
from cloze.core.pipeline import PageResult, build, prepare
from cloze.core.select.gemini import AskSelectorLLM
from cloze.infra.gemini import GeminiClient


def run_pipeline(
    pdf: bytes, policy: Policy, out: Path, *, rule_only: bool, dump_stages: bool
) -> None:
    asyncio.run(_run(pdf, policy, out, rule_only=rule_only, dump_stages=dump_stages))


async def _run(
    pdf: bytes, policy: Policy, out: Path, *, rule_only: bool, dump_stages: bool
) -> None:
    pages = prepare(pdf)
    client = None if rule_only else GeminiClient(load_settings())
    factory: Callable[[Literal["select"], int], AskSelectorLLM] | None = None
    if client is not None:
        ask = client.ask_llm

        def select_for(_kind: Literal["select"], _page_no: int) -> AskSelectorLLM:
            return ask

        factory = select_for
    try:
        async for result in build(
            pages, policy, factory, skip_pages=set(), analysis=None, avoid_ids={}
        ):
            _write_result(result, out, dump_stages=dump_stages)
            print(f"{result.page_no:>4} | {result.source:>6} | {len(result.blanks):>3}")
    finally:
        if client is not None:
            await client.aclose()


def _write_result(result: PageResult, out: Path, *, dump_stages: bool) -> None:
    stem = f"page_{result.page_no:03d}"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "extract_version": EXTRACT_VERSION,
        **result.tokens.model_dump(mode="json"),
        "blanks": [blank.model_dump(mode="json") for blank in result.blanks],
        "selection": {
            "source": result.source,
            "attempts": result.attempts,
            "quality": {"survival_ratio": result.survival_ratio},
        },
    }
    (out / f"{stem}.webp").write_bytes(result.image_webp)
    (out / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if dump_stages:
        stages = {
            "extract": result.tokens.model_dump(mode="json"),
            "filter": result.candidates.model_dump(mode="json"),
            "selection": payload["selection"],
            "gate": asdict(result.gate)
            | {"blanks": [blank.model_dump(mode="json") for blank in result.gate.blanks]}
            if result.gate is not None
            else None,
        }
        (out / f"{stem}.stages.json").write_text(
            json.dumps(stages, ensure_ascii=False), encoding="utf-8"
        )
