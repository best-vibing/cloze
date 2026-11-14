"""품질 게이트 기준값 보정(#40) 1차용 라벨 시트. 라벨 기준은 docs/design/label-guide.md.

uv run python -m cloze.cli.label_sheet make [--out FILE] [--force]
uv run python -m cloze.cli.label_sheet check FILE

make: CLOZE_SAMPLES_DIR 폴더의 PDF마다 규칙 기반 선택 결과를 뽑아 빈칸 한 칸이 한 줄인 CSV로 쓴다.
seed·density를 고정해 다시 뽑아도 같은 결과가 나온다. label·labeler 칸은 사람이 채운다.
check: 라벨을 채운 CSV에서 label 값이 좋음 / 애매 / 나쁨 중 하나인지, labeler가 있는지 본다.
"""

import argparse
import csv
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from cloze.cli.__main__ import _answers
from cloze.config import load_settings
from cloze.core.extract import EXTRACT_VERSION, extract_tokens
from cloze.core.filter import filter_candidates, word_key
from cloze.core.models import DEFAULT_DENSITY, PageTokens, Policy, Selection, Token
from cloze.core.select.rule import (
    DocStats,
    bold_signal,
    build_doc_stats,
    frequency_signal,
    heading_signal,
    score_token,
    select_rule,
    size_signal,
    target_count,
)

# 라벨러끼리, 그리고 다시 뽑을 때 결과가 같도록 고정한다. CLI 기본값과 같다.
SEED = 0
DENSITY = DEFAULT_DENSITY
LABELS = ("좋음", "애매", "나쁨")
# 정답 앞뒤로 보여 줄 토큰 수. 다른 빈칸은 [글자]로 표시한다.
CONTEXT_TOKENS = 6
DEFAULT_OUT = Path("out/labels/rule.csv")
COLUMNS = (
    "file",
    "page",
    "order",
    "answer",
    "context_before",
    "context_after",
    "extract_version",
    "answer_len",
    "hangul",
    "exposure_count",
    "page_target",
    "page_survival",
    "score",
    "size_signal",
    "bold_signal",
    "heading_signal",
    "frequency_signal",
    "label",
    "labeler",
)
MISSING_SAMPLES = (
    "CLOZE_SAMPLES_DIR이 비어 있다. 수업 PDF를 둔 폴더를 .env나 환경 변수로 지정한다.\n"
    "예: CLOZE_SAMPLES_DIR=C:/samples (PDF는 레포에 넣지 않는다)"
)

type Row = dict[str, str | int | float]


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "make":
        _make(args.out, args.force)
    else:
        _check(args.file)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloze.cli.label_sheet")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("make", help="CLOZE_SAMPLES_DIR의 PDF로 라벨 시트를 만든다")
    make.add_argument("--out", type=Path, default=DEFAULT_OUT)
    make.add_argument("--force", action="store_true", help="이미 있는 파일을 덮어쓴다")
    check = commands.add_parser("check", help="라벨을 채운 시트의 label·labeler 값을 검사한다")
    check.add_argument("file", type=Path)
    return parser


def _make(out: Path, force: bool) -> None:
    pdfs = _sample_pdfs(load_settings().samples_dir)
    if out.exists() and not force:
        raise SystemExit(f"{out}이 이미 있다. 채운 라벨을 지키려고 멈춘다(덮어쓰려면 --force).")
    rows = [row for pdf in pdfs for row in pdf_rows(pdf)]
    out.parent.mkdir(parents=True, exist_ok=True)
    # 엑셀이 한글을 깨뜨리지 않도록 BOM을 붙인다. 읽을 때는 encoding="utf-8-sig"로 연다.
    with out.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(pdfs)}개 PDF, {len(rows)}칸 → {out} (seed={SEED}, density={DENSITY})")


def _sample_pdfs(samples_dir: Path | None) -> list[Path]:
    if samples_dir is None:
        raise SystemExit(MISSING_SAMPLES)
    if not samples_dir.is_dir():
        raise SystemExit(f"CLOZE_SAMPLES_DIR 폴더가 없다: {samples_dir}")
    pdfs = sorted(path for path in samples_dir.iterdir() if path.suffix.lower() == ".pdf")
    if not pdfs:
        raise SystemExit(f"CLOZE_SAMPLES_DIR 폴더에 PDF가 없다: {samples_dir}")
    return pdfs


def pdf_rows(pdf: Path) -> list[Row]:
    """PDF 하나의 빈칸마다 한 줄. order는 cli mask가 쓰는 칸 순서(1부터)와 같다."""
    policy = Policy(preset="concept", density=DENSITY, seed=SEED)
    pages = extract_tokens(pdf.read_bytes())
    candidates = filter_candidates(pages, policy)
    stats = build_doc_stats(pages, candidates)
    rows: list[Row] = []
    for page, page_candidates in zip(pages, candidates, strict=True):
        selection = select_rule(page, page_candidates, policy, set(), stats)
        target = target_count(page, page_candidates, policy)
        rows += _page_rows(pdf.name, page, selection, target, stats)
    return rows


def _page_rows(
    name: str, page: PageTokens, selection: Selection, target: int, stats: DocStats
) -> list[Row]:
    median = statistics.median(token.font_size for token in page.tokens) if page.tokens else 1.0
    survival = round(len(selection.spans) / target, 4) if target else 0.0
    blank_ids = {id_ for span in selection.spans for id_ in span}
    visible = Counter(word_key(t.text) for t in page.tokens if t.id not in blank_ids)
    position = {token.id: index for index, token in enumerate(page.tokens)}
    rows: list[Row] = []
    for order, (span, answer) in enumerate(
        zip(selection.spans, _answers(page, selection), strict=True), start=1
    ):
        token = page.tokens[position[span[0]]]
        key = word_key(token.text)
        before, after = _context(page.tokens, position[span[0]], blank_ids)
        rows.append(
            {
                "file": name,
                "page": page.page.page_no,
                "order": order,
                "answer": answer,
                "context_before": before,
                "context_after": after,
                "extract_version": EXTRACT_VERSION,
                "answer_len": len(key),
                "hangul": int(any("가" <= char <= "힣" for char in key)),
                "exposure_count": visible[key],
                "page_target": target,
                "page_survival": survival,
                **_signals(token, median, stats),
                "label": "",
                "labeler": "",
            }
        )
    return rows


def _signals(token: Token, median: float, stats: DocStats) -> Row:
    return {
        "score": round(score_token(token, median, stats), 4),
        "size_signal": round(size_signal(token, median), 4),
        "bold_signal": bold_signal(token),
        "heading_signal": heading_signal(token, stats),
        "frequency_signal": round(frequency_signal(token, stats), 4),
    }


def _context(tokens: list[Token], index: int, blank_ids: set[int]) -> tuple[str, str]:
    def show(token: Token) -> str:
        return f"[{token.text}]" if token.id in blank_ids else token.text

    before = tokens[max(0, index - CONTEXT_TOKENS) : index]
    after = tokens[index + 1 : index + 1 + CONTEXT_TOKENS]
    return " ".join(map(show, before)), " ".join(map(show, after))


def _check(path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as file:
        records = list(csv.DictReader(file))
    problems = check_rows(records)
    for problem in problems:
        print(problem, file=sys.stderr)
    counts = Counter(record.get("label", "") for record in records)
    summary = ", ".join(f"{label} {counts[label]}" for label in LABELS)
    print(f"{len(records)}칸: {summary}, 빈 칸 {counts['']}")
    if problems:
        raise SystemExit(1)


def check_rows(records: list[dict[str, str]]) -> list[str]:
    """줄 번호는 머리글을 1줄로 센다. 아직 안 채운 칸(label 빈 칸)은 문제로 보지 않는다."""
    if records and not {"label", "labeler"} <= records[0].keys():
        return ["label·labeler 열이 없다"]
    problems: list[str] = []
    for line, record in enumerate(records, start=2):
        label = record["label"].strip()
        if label and label not in LABELS:
            problems.append(f"{line}줄: label '{label}'은 {' / '.join(LABELS)} 중 하나가 아니다")
        if label and not record["labeler"].strip():
            problems.append(f"{line}줄: labeler가 비어 있다")
    return problems


if __name__ == "__main__":
    main()
