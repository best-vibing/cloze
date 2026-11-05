"""확인용 CLI: uv run python -m cloze.cli select <pdf> [--density N] [--seed N] --out DIR"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from cloze.core.extract import extract_tokens
from cloze.core.filter import filter_candidates
from cloze.core.models import (
    DEFAULT_DENSITY,
    SCHEMA_VERSION,
    PageCandidates,
    PageTokens,
    Policy,
    Selection,
)
from cloze.core.select.rule import build_doc_stats, select_rule


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    policy = Policy(preset="concept", density=args.density, seed=args.seed)
    _run_select(args.pdf, policy, args.out)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloze.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select", help="페이지별 빈칸 토큰을 골라 JSON으로 저장한다")
    select.add_argument("pdf", type=Path)
    select.add_argument("--density", type=float, default=DEFAULT_DENSITY)
    select.add_argument("--seed", type=int, default=0)
    select.add_argument("--out", type=Path, required=True)
    return parser


def _run_select(pdf: Path, policy: Policy, out: Path) -> None:
    pages = extract_tokens(pdf.read_bytes())
    candidates = filter_candidates(pages, policy)
    stats = build_doc_stats(pages, candidates)
    out.mkdir(parents=True, exist_ok=True)
    print("page | candidates / selected | words")
    for page, page_candidates in zip(pages, candidates, strict=True):
        selection = select_rule(page, page_candidates, policy, set(), stats)
        _write_page(out, page, page_candidates, selection)
        print(_table_row(page, page_candidates, selection))


def _write_page(
    out: Path, page: PageTokens, candidates: PageCandidates, selection: Selection
) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "page": page.page.model_dump(mode="json"),
        "tokens": [token.model_dump(mode="json") for token in page.tokens],
        "candidates": candidates.candidate_ids,
        "excluded": [item.model_dump(mode="json") for item in candidates.excluded],
        "selection": selection.model_dump(mode="json"),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    (out / f"page_{page.page.page_no:03d}.json").write_text(text + "\n", encoding="utf-8")


def _table_row(page: PageTokens, candidates: PageCandidates, selection: Selection) -> str:
    texts = {token.id: token.text for token in page.tokens}
    words = ", ".join(texts[id_] for span in selection.spans for id_ in span)
    counts = f"{len(candidates.candidate_ids)} / {len(selection.spans)}"
    return f"{page.page.page_no:>4} | {counts:>21} | {words}"


if __name__ == "__main__":
    main()
