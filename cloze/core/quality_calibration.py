"""Offline calibration calculations; no file access or evaluator consensus policy."""

import csv
import math
import operator
from collections.abc import Sequence
from io import StringIO

type Key = tuple[str, ...]
type Sample = tuple[Key, str, float]
type Count = tuple[int, int, float | None]  # removed, total, rate
type Evaluation = dict[str, Count]
LABELS = ("좋음", "애매", "나쁨")
_OPERATORS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def _validate_sequence(value: object) -> None:
    """Reject scalar and text inputs before sequence operations."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("Expected a non-text sequence")


def _validate_sample(sample: object) -> None:
    """Require exactly the three fields of a calibration sample."""
    _validate_sequence(sample)
    if not isinstance(sample, (tuple, list)) or len(sample) != 3:
        raise ValueError("Each sample needs exactly key, label and numeric value")


def _validate_sequences(*values: object) -> None:
    """Validate aligned input containers before reading their lengths."""
    for value in values:
        _validate_sequence(value)


def _comparison(comparison: str) -> None:
    """Validate the explicit comparison operator before dictionary lookup."""
    if not isinstance(comparison, str) or comparison not in _OPERATORS:
        raise ValueError("Comparison must be <, <=, > or >=")


def _number(value: float) -> float:
    if type(value) not in (int, float):
        raise ValueError("Expected a finite number, not a boolean or string")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("Number is too large") from exc
    if not math.isfinite(result):
        raise ValueError("Expected a finite number")
    return result


def _valid_key_part(part: object) -> bool:
    return isinstance(part, str) and bool(part.strip())


def _validate_key(key: Key) -> None:
    if not isinstance(key, tuple) or not key:
        raise ValueError("Each blank needs a nonempty tuple key")
    if not all(map(_valid_key_part, key)):
        raise ValueError("Key components must be nonempty strings")


def _unique_keys(keys: Sequence[Key]) -> None:
    seen: set[Key] = set()
    for key in keys:
        _validate_key(key)
        if key in seen:
            raise ValueError("Duplicate blank key; evaluator consensus is not implemented")
        seen.add(key)


def _summarize(totals: dict[str, int], rejected: dict[str, int]) -> Evaluation:
    return {
        label: (rejected[label], total, rejected[label] / total if total else None)
        for label, total in totals.items()
    }


def evaluate_removals(
    keys: Sequence[Key], labels: Sequence[str], removed: Sequence[bool]
) -> Evaluation:
    """Count removals separately for each label without modifying inputs.

    Args:
        keys: Unique blank identities, excluding evaluator identity. Supply one
            evaluator's data or externally agreed labels; never pooled raw ratings.
        labels: One of 좋음, 애매, 나쁨 per blank, before removal.
        removed: Actual boolean removal decisions, aligned with keys and labels.

    Returns:
        Label to (removed count, total count, rate). Empty denominators yield None.
        애매 is counted separately and never enters the other denominators.

    Raises:
        ValueError: Lengths differ, keys repeat, or labels/decisions are invalid.
    """
    _validate_sequences(keys, labels, removed)
    if not len(keys) == len(labels) == len(removed):
        raise ValueError("Keys, labels and removal decisions must have equal lengths")
    _unique_keys(keys)
    totals = dict.fromkeys(LABELS, 0)
    rejected = dict.fromkeys(LABELS, 0)
    for label, decision in zip(labels, removed, strict=True):
        if label not in LABELS:
            raise ValueError("Invalid label")
        if type(decision) is not bool:
            raise ValueError("Removal decisions must be booleans")
        totals[label] += 1
        rejected[label] += int(decision)
    return _summarize(totals, rejected)


def bad_removal_rate(
    keys: Sequence[Key], labels: Sequence[str], removed: Sequence[bool]
) -> float | None:
    """Return removed bad blanks / all bad blanks.

    Args:
        keys: Blank identities as specified by evaluate_removals.
        labels: Aligned labels before removal.
        removed: Aligned boolean decisions.

    Returns:
        A rate in [0, 1], or None when no bad blanks exist.

    Raises:
        ValueError: Inputs violate evaluate_removals validation.
    """
    return evaluate_removals(keys, labels, removed)["나쁨"][2]


def good_removal_rate(
    keys: Sequence[Key], labels: Sequence[str], removed: Sequence[bool]
) -> float | None:
    """Return removed good blanks / all good blanks (false removals).

    Args:
        keys: Blank identities as specified by evaluate_removals.
        labels: Aligned labels before removal.
        removed: Aligned boolean decisions.

    Returns:
        A rate in [0, 1], or None when no good blanks exist.

    Raises:
        ValueError: Inputs violate evaluate_removals validation.
    """
    return evaluate_removals(keys, labels, removed)["좋음"][2]


def compare_thresholds(
    samples: Sequence[Sample], thresholds: Sequence[float], *, comparison: str
) -> list[tuple[float, Evaluation]]:
    """Evaluate removal condition value <operator> threshold, preserving order.

    Args:
        samples: (blank identity, label, already calculated numeric value) tuples.
        thresholds: Finite candidates, including duplicates if desired.
        comparison: Explicit <, <=, > or >=; no direction is inferred.

    Returns:
        Candidate and counts/rates pairs in candidate order; no winner is chosen.

    Raises:
        ValueError: Operator, numeric values or evaluation inputs are invalid.
    """
    _comparison(comparison)
    _validate_sequence(samples)
    _validate_sequence(thresholds)
    keys: list[Key] = []
    labels: list[str] = []
    values: list[float] = []
    for sample in samples:
        _validate_sample(sample)
        keys.append(sample[0])
        labels.append(sample[1])
        values.append(_number(sample[2]))
    evaluate_removals(keys, labels, [False] * len(samples))
    candidates = list(map(_number, thresholds))
    compare = _OPERATORS[comparison]
    return [
        (candidate, evaluate_removals(keys, labels, [compare(v, candidate) for v in values]))
        for candidate in candidates
    ]


def _csv_rows(text: str) -> list[list[str]]:
    if not isinstance(text, str):
        raise ValueError("Expected decoded CSV text")
    reader = csv.reader(StringIO(text, newline=""), strict=True)
    try:
        return list(reader)
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV near line {reader.line_num}") from exc


def _columns(header: list[str], required: Sequence[str]) -> list[int]:
    if not header or not all(map(str.strip, header)):
        raise ValueError("Missing header")
    if len(set(header)) != len(header):
        raise ValueError("Duplicate header")
    if not set(required).issubset(header):
        raise ValueError("Missing required column")
    return list(map(header.index, required))


def _csv_sample(row: list[str], indices: list[int], line: int) -> Sample:
    cells = [row[index] for index in indices]
    if any(not cell.strip() for cell in cells):
        raise ValueError(f"Missing required value in record {line}")
    try:
        value = _number(float(cells[-1]))
    except ValueError as exc:
        raise ValueError(f"Invalid numeric value in record {line}") from exc
    return tuple(cells[:-2]), cells[-2], value


def _csv_config(
    key_columns: Sequence[str], required: list[str], bounds: tuple[float, float] | None
) -> tuple[float, float]:
    if not key_columns or len(set(required)) != len(required):
        raise ValueError("Select distinct key, label and value columns")
    if bounds is None:
        return -math.inf, math.inf
    _validate_sequence(bounds)
    lower, upper = map(_number, bounds)
    if lower > upper:
        raise ValueError("Range minimum exceeds maximum")
    return lower, upper


def _parse_records(
    rows: list[list[str]], indices: list[int], lower: float, upper: float
) -> list[Sample]:
    samples: list[Sample] = []
    for line, row in enumerate(rows[1:], start=2):
        if len(row) != len(rows[0]):
            raise ValueError(f"Column count mismatch in record {line}")
        sample = _csv_sample(row, indices, line)
        if not lower <= sample[2] <= upper:
            raise ValueError(f"Numeric value outside range in record {line}")
        samples.append(sample)
    return samples


def parse_label_csv(
    text: str,
    *,
    key_columns: Sequence[str],
    label_column: str,
    value_column: str,
    value_range: tuple[float, float] | None = None,
) -> list[Sample]:
    """Validate CSV text with caller-selected columns; ignore unused columns.

    Args:
        text: Already decoded CSV text. Cells are never executed or opened.
        key_columns: Blank identity columns, excluding evaluator identity. Raw
            multiple-evaluator ratings must be separated before calling.
        label_column: Column containing an exact permitted label.
        value_column: Column containing a finite numeric value.
        value_range: Optional inclusive (minimum, maximum), both finite.

    Returns:
        Internal sample tuples, not a shared CSV contract. Header-only gives [].

    Raises:
        ValueError: CSV structure, selected columns, identities, labels, numbers
            or range are invalid. No rows are silently skipped or aggregated.
    """
    _validate_sequence(key_columns)
    required = [*key_columns, label_column, value_column]
    if not all(map(_valid_key_part, required)):
        raise ValueError("Column names must be nonempty strings")
    lower, upper = _csv_config(key_columns, required, value_range)
    rows = _csv_rows(text)
    indices = _columns(rows[0] if rows else [], required)
    samples = _parse_records(rows, indices, lower, upper)
    evaluate_removals([s[0] for s in samples], [s[1] for s in samples], [False] * len(samples))
    return samples
