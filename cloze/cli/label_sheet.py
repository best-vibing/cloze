"""품질 게이트 기준값 보정(#40) 1차용 라벨 시트. 라벨 기준은 docs/design/label-guide.md.

uv run python -m cloze.cli.label_sheet make [--out FILE] [--force]
uv run python -m cloze.cli.label_sheet check FILE

make: CLOZE_SAMPLES_DIR 폴더의 PDF마다 규칙 기반 선택 결과를 뽑아 빈칸 한 칸이 한 줄인 CSV로 쓴다.
seed·density를 고정해 다시 뽑아도 같은 결과가 나온다. label·labeler 칸은 사람이 채운다.
check: 라벨을 채운 CSV에서 label 값이 좋음 / 애매 / 나쁨 중 하나인지, labeler가 있는지 본다.
"""

import argparse
import csv
import math
import re
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

from pdfplumber.utils.exceptions import PdfminerException

from cloze.cli.__main__ import _answers
from cloze.config import load_settings
from cloze.core.extract import EXTRACT_VERSION, extract_tokens
from cloze.core.filter import filter_candidates, word_key
from cloze.core.models import DEFAULT_DENSITY, PageTokens, Policy, Selection, Token
from cloze.core.quality_calibration import LABELS
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
INTEGER_COLUMNS = (
    "page",
    "order",
    "extract_version",
    "answer_len",
    "hangul",
    "exposure_count",
    "page_target",
)
UNIT_COLUMNS = (
    "page_survival",
    "score",
    "size_signal",
    "bold_signal",
    "heading_signal",
    "frequency_signal",
)


def _integer(column: str, value: str) -> str:
    """정수 열을 검증하고 같은 숫자를 나타내는 식별자를 정규화한다."""
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError(f"{column}: expected a nonnegative integer")
    number = int(value)
    minimum = int(column in INTEGER_COLUMNS[:3])
    maximum = 1 if column == "hangul" else number
    if not minimum <= number <= maximum:
        raise ValueError(f"{column}: integer outside permitted range")
    return str(number)


def _numbers(record: dict[str, str]) -> None:
    """기존 정수 열과 비율·신호 열의 자료형과 범위를 검증한다."""
    for column in INTEGER_COLUMNS:
        record[column] = _integer(column, record[column])
    for column in UNIT_COLUMNS:
        try:
            value = float(record[column])
        except ValueError:
            raise ValueError(f"{column}: expected a number") from None
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{column}: expected a finite value in [0, 1]")


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
    rows: list[Row] = []
    for pdf in pdfs:
        try:
            pdf_result = pdf_rows(pdf)
        except (OSError, PdfminerException) as error:
            raise SystemExit(f"{pdf.name}: PDF를 읽을 수 없다: {error}") from None
        rows.extend(pdf_result)
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
    pdfs = sorted(
        path for path in samples_dir.iterdir() if path.suffix.lower() == ".pdf" and path.is_file()
    )
    if not pdfs:
        raise SystemExit(f"CLOZE_SAMPLES_DIR 폴더에 PDF가 없다: {samples_dir}")
    return pdfs


def pdf_rows(pdf: Path) -> list[Row]:
    """PDF 하나의 빈칸마다 한 줄. order는 cli mask가 쓰는 칸 순서(1부터)와 같다."""
    policy = Policy(preset="concept", density=DENSITY, seed=SEED)
    pages = extract_tokens(pdf.read_bytes())
    if not any(page.tokens for page in pages):
        print(f"{pdf.name}: 추출된 텍스트가 없다", file=sys.stderr)
        return []
    candidates = filter_candidates(pages, policy)
    stats = build_doc_stats(pages, candidates)
    rows: list[Row] = []
    for page, page_candidates in zip(pages, candidates, strict=True):
        selection = select_rule(page, page_candidates, policy, set(), stats)
        target = target_count(page, page_candidates, policy)
        rows += _page_rows(pdf.name, page, selection, target, stats)
    if not rows:
        print(f"{pdf.name}: 규칙 선택기가 고른 빈칸이 없다", file=sys.stderr)
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
    try:
        records, problems = read_sheet(path.read_text(encoding="utf-8-sig"), completed=False)
    except (ValueError, OSError) as error:
        records, problems = [], [str(error)]
    for problem in problems:
        print(problem, file=sys.stderr)
    counts = Counter((record.get("label") or "").strip() for record in records)
    summary = ", ".join(f"{label} {counts[label]}" for label in LABELS)
    print(f"{len(records)}칸: {summary}, 빈 칸 {counts['']}")
    if problems:
        raise SystemExit(1)


def read_sheet(
    text: str,
    *,
    completed: bool,
) -> tuple[list[dict[str, str]], list[str]]:
    """기존 시트를 검증한다. 중간 검사에서는 아직 라벨이 없는 행도 허용한다."""
    reader = csv.DictReader(StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    try:
        header = reader.fieldnames or []
        records = list(reader)
    except csv.Error as error:
        raise ValueError(f"{reader.line_num}줄: 잘못된 CSV 인용 구문") from error
    if not all(map(str.strip, header)) or len(set(header)) != len(header):
        return records, ["비어 있거나 중복된 열 이름 (blank or duplicate header)"]
    if not completed and set(header) == {"label", "labeler"}:
        return records, check_rows(records)
    if not set(COLUMNS) <= set(header):
        return records, ["필수 열 누락 (missing label-sheet columns)"]
    return records, check_rows(records) + _check_full_records(records, completed)


def _check_full_records(records: list[dict[str, str]], completed: bool) -> list[str]:
    problems: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    versions: set[str] = set()
    for line, record in enumerate(records, start=2):
        if not all(isinstance(value, str) for value in record.values()):
            continue  # check_rows reports column count mismatches.
        try:
            _check_full_record(record, completed, seen)
            versions.add(record["extract_version"])
        except ValueError as error:
            problems.append(f"{line}줄: {error}")
    if len(versions) > 1:
        problems.append("한 시트에 서로 다른 extract_version이 있다. 버전별로 나누어 검사한다.")
    return problems


def _check_full_record(
    record: dict[str, str],
    completed: bool,
    seen: set[tuple[str, str, str, str]],
) -> None:
    _numbers(record)
    for column in ("file", "answer"):
        if not record[column].strip():
            raise ValueError(f"Missing {column}")
    if completed and not record["label"]:
        raise ValueError("Missing label")
    record["labeler"] = record["labeler"].strip()
    if record["label"]:
        key = (record["labeler"], record["file"].strip(), record["page"], record["order"])
        if key in seen:
            raise ValueError("Duplicate blank for the same evaluator")
        seen.add(key)


def check_rows(records: list[dict[str, str]]) -> list[str]:
    """줄 번호는 머리글을 1줄로 센다. 아직 안 채운 칸(label 빈 칸)은 문제로 보지 않는다."""
    if records and not {"label", "labeler"} <= records[0].keys():
        return ["label·labeler 열이 없다"]
    problems: list[str] = []
    for line, record in enumerate(records, start=2):
        # DictReader는 모자란 칸을 None, 남는 칸을 키 None의 리스트로 채운다.
        if not all(isinstance(value, str) for value in record.values()):
            problems.append(f"{line}줄: 열 수가 머리글과 다르다")
            continue
        # 보정 계산(parse_label_csv)은 label을 strip하지 않고 그대로 비교한다.
        label = record["label"]
        if label != label.strip():
            problems.append(f"{line}줄: label '{label}' 앞뒤에 공백이 있다")
        elif label and label not in LABELS:
            problems.append(f"{line}줄: label '{label}'은 {' / '.join(LABELS)} 중 하나가 아니다")
        if label and not record["labeler"].strip():
            problems.append(f"{line}줄: labeler가 비어 있다")
    return problems


if __name__ == "__main__":
    main()
