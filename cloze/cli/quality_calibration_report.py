"""라벨 시트를 평가자별로 비교한다. 임계값이나 평가자 합의는 결정하지 않는다."""

import argparse
import csv
from collections import defaultdict
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

from cloze.cli.label_sheet import INTEGER_COLUMNS, UNIT_COLUMNS, read_sheet
from cloze.core.quality_calibration import Evaluation, compare_thresholds, parse_label_csv

METRICS = (*INTEGER_COLUMNS[3:], *UNIT_COLUMNS)


def _groups(text: str) -> dict[str, list[dict[str, str]]]:
    records, problems = read_sheet(text, completed=True)
    if problems:
        raise ValueError(problems[0])
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        groups[record["labeler"]].append(record)
    return dict(groups)


def _samples(records: list[dict[str, str]], metric: str) -> str:
    """기존 CSV 계산기에 전달할 평가자 한 명의 시트를 메모리에 만든다."""
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=("file", "page", "order", "label", metric))
    writer.writeheader()
    writer.writerows({name: record[name] for name in writer.fieldnames} for record in records)
    return stream.getvalue()


def compare_sheet(
    text: str, metric: str, thresholds: Sequence[float], *, comparison: str
) -> dict[str, list[tuple[float, Evaluation]]]:
    """기존 계산식을 사용해 평가자별 후보 결과를 반환한다.

    Args:
        text: 기존 19개 열을 포함하는 UTF-8 라벨 CSV 텍스트.
        metric: 비교할 기존 숫자 지표 열.
        thresholds: 순서와 중복을 보존할 유한한 후보값.
        comparison: 제거 조건의 명시적인 <, <=, > 또는 >=.

    Returns:
        평가자별 후보와 라벨별 제거 수·전체 수·비율. 빈 시트는 빈 dict다.

    Raises:
        ValueError: 시트, 지표, 후보 또는 비교 연산자가 잘못된 경우.
        csv.Error: CSV 인용 구문이 잘못된 경우.
    """
    if metric not in METRICS:
        raise ValueError("Unknown numeric metric")
    compare_thresholds([], thresholds, comparison=comparison)
    results: dict[str, list[tuple[float, Evaluation]]] = {}
    for labeler, records in _groups(text).items():
        samples = parse_label_csv(
            _samples(records, metric),
            key_columns=("file", "page", "order"),
            label_column="label",
            value_column=metric,
        )
        results[labeler] = compare_thresholds(samples, thresholds, comparison=comparison)
    return results


def _rate(rate: float | None) -> str:
    """분모가 없는 비율과 실제 0을 구분한다."""
    return "N/A" if rate is None else f"{rate:.4f}"


def _print_table(results: dict[str, list[tuple[float, Evaluation]]], metric: str, op: str) -> None:
    """분모가 없는 비율은 N/A로 표시하고 애매 라벨을 별도 행에 출력한다."""
    print("labeler\tmetric\toperator\tthreshold\tlabel\tremoved\ttotal\trate")
    for labeler, candidates in results.items():
        for threshold, evaluation in candidates:
            for label, (removed, total, rate) in evaluation.items():
                formatted = _rate(rate)
                print(
                    f"{labeler}\t{metric}\t{op}\t{threshold:g}\t{label}\t"
                    f"{removed}\t{total}\t{formatted}"
                )
    if not results:
        print("No labeled rows.")


def main(argv: Sequence[str] | None = None) -> None:
    """기존 시트를 읽어 후보 비교 표를 출력하며 파일을 변경하지 않는다."""
    parser = argparse.ArgumentParser(prog="cloze.cli.quality_calibration_report")
    parser.add_argument("file", type=Path)
    parser.add_argument("--metric", choices=METRICS, required=True)
    parser.add_argument("--comparison", choices=("<", "<=", ">", ">="), required=True)
    parser.add_argument("--thresholds", nargs="+", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        results = compare_sheet(
            args.file.read_text(encoding="utf-8-sig"),
            args.metric,
            args.thresholds,
            comparison=args.comparison,
        )
    except (ValueError, csv.Error, OSError) as error:
        parser.error(str(error))
    _print_table(results, args.metric, args.comparison)


if __name__ == "__main__":
    main()
