"""확인용 CLI.

uv run python -m cloze.cli select <pdf> [--density N] [--seed N] --out DIR
uv run python -m cloze.cli mask <pdf> --out DIR [--density N] [--seed N] [--debug-boxes]
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from cloze.core.extract import EXTRACT_VERSION, extract_tokens
from cloze.core.extract.render import draw_boxes, render_page
from cloze.core.filter import filter_candidates, strip_punctuation
from cloze.core.models import (
    DEFAULT_DENSITY,
    SCHEMA_VERSION,
    PageCandidates,
    PageTokens,
    Policy,
    Selection,
)
from cloze.core.redact import blank_bbox, encode_webp, redact
from cloze.core.select.rule import build_doc_stats, select_rule

type PageRun = tuple[PageTokens, PageCandidates, Selection]


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    policy = Policy(preset="concept", density=args.density, seed=args.seed)
    pdf = args.pdf.read_bytes()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "mask":
        _run_mask(pdf, policy, args.out, args.debug_boxes)
    else:
        _run_select(pdf, policy, args.out)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloze.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_common(commands.add_parser("select", help="페이지별 빈칸 토큰을 골라 JSON으로 저장한다"))
    mask = commands.add_parser("mask", help="빈칸 자리를 칠한 페이지 이미지를 만든다")
    _add_common(mask)
    mask.add_argument("--debug-boxes", action="store_true", help="원본에 박스를 그린 PNG도 남긴다")
    return parser


def _add_common(command: argparse.ArgumentParser) -> None:
    command.add_argument("pdf", type=Path)
    command.add_argument("--density", type=float, default=DEFAULT_DENSITY)
    command.add_argument("--seed", type=int, default=0)
    command.add_argument("--out", type=Path, required=True)


def _select_pages(pdf: bytes, policy: Policy) -> list[PageRun]:
    pages = extract_tokens(pdf)
    candidates = filter_candidates(pages, policy)
    stats = build_doc_stats(pages, candidates)
    return [
        (page, page_candidates, select_rule(page, page_candidates, policy, set(), stats))
        for page, page_candidates in zip(pages, candidates, strict=True)
    ]


def _run_select(pdf: bytes, policy: Policy, out: Path) -> None:
    print("page | candidates / selected | words")
    for page, candidates, selection in _select_pages(pdf, policy):
        _write_json(
            out / f"page_{page.page.page_no:03d}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "page": page.page.model_dump(mode="json"),
                "tokens": [token.model_dump(mode="json") for token in page.tokens],
                "candidates": candidates.candidate_ids,
                "excluded": [item.model_dump(mode="json") for item in candidates.excluded],
                "selection": selection.model_dump(mode="json"),
            },
        )
        counts = f"{len(candidates.candidate_ids)} / {len(selection.spans)}"
        print(f"{page.page.page_no:>4} | {counts:>21} | {', '.join(_answers(page, selection))}")


def _run_mask(pdf: bytes, policy: Policy, out: Path, debug_boxes: bool) -> None:
    print("page | blanks | answers")
    for page, _, selection in _select_pages(pdf, policy):
        _mask_page(pdf, page, selection, out, debug_boxes)
        answers = _answers(page, selection)
        print(f"{page.page.page_no:>4} | {len(answers):>6} | {', '.join(answers)}")


def _mask_page(
    pdf: bytes, page: PageTokens, selection: Selection, out: Path, debug_boxes: bool
) -> None:
    """원본 이미지는 debug 옵션일 때만 (박스를 그린 채로) 파일로 남긴다."""
    stem = f"page_{page.page.page_no:03d}"
    boxes = [blank_bbox(page, span) for span in selection.spans]
    image = render_page(pdf, page.page.page_no)
    result = redact(image, boxes)
    (out / f"{stem}.webp").write_bytes(encode_webp(result.image))
    blanks = [
        {"order": order, "answer": answer, "bbox": list(box), "bg_color": color}
        for order, (answer, box, color) in enumerate(
            zip(_answers(page, selection), boxes, result.colors, strict=True), start=1
        )
    ]
    payload = {"extract_version": EXTRACT_VERSION, "page": page.page.model_dump(mode="json")}
    _write_json(out / f"{stem}.json", {**payload, "blanks": blanks})
    if debug_boxes:
        draw_boxes(image, boxes).save(out / f"{stem}.debug.png")


def _answers(page: PageTokens, selection: Selection) -> list[str]:
    """칸마다 정답: 묶인 토큰 원문에서 앞뒤 문장 부호를 떼고 공백으로 잇는다.
    한국어 조사는 떼지 않는다(v1은 어절 단위, grading-and-overlay.md 1.1절)."""
    texts = {token.id: strip_punctuation(token.text) for token in page.tokens}
    return [" ".join(texts[id_] for id_ in span) for span in selection.spans]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
