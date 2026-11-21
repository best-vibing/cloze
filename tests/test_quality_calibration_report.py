"""기존 라벨 시트와 보정 계산기의 연결 및 CLI 오류를 검증한다."""

import csv
from io import StringIO
from pathlib import Path

import pytest

from cloze.cli.label_sheet import COLUMNS, pdf_rows
from cloze.cli.quality_calibration_report import METRICS, compare_sheet, main
from cloze.core.quality_calibration import compare_thresholds, evaluate_removals


def record(label: str = "좋음", labeler: str = "alice", **changes: str) -> dict[str, str]:
    """시트 생성기의 열 이름을 사용하는 합성 라벨 행을 만든다."""
    row = dict.fromkeys(COLUMNS, "0")
    row.update(
        file="sample.pdf",
        page="1",
        order="1",
        answer="term",
        extract_version="3",
        label=label,
        labeler=labeler,
    )
    row.update(changes)
    return row


def sheet(*rows: dict[str, str], header: tuple[str, ...] = COLUMNS) -> str:
    """실제 CSV 인용 처리를 사용하는 시트를 메모리에 만든다."""
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def test_independent_evaluators_and_existing_calculations() -> None:
    text = sheet(
        record("나쁨", score="0.2"),
        record("좋음", order="2", score="0.8"),
        record("애매", order="3", score="0.4"),
        record("좋음", "bob", score="0.2"),
    )
    results = compare_sheet(text, "score", [0.5, 0.1, 0.5], comparison="<")
    samples = [
        (("sample.pdf", "1", "1"), "나쁨", 0.2),
        (("sample.pdf", "1", "2"), "좋음", 0.8),
        (("sample.pdf", "1", "3"), "애매", 0.4),
    ]
    assert results["alice"] == compare_thresholds(samples, [0.5, 0.1, 0.5], comparison="<")
    expected = evaluate_removals(
        [("sample.pdf", "1", "1"), ("sample.pdf", "1", "2"), ("sample.pdf", "1", "3")],
        ["나쁨", "좋음", "애매"],
        [True, False, True],
    )
    assert results["alice"][0][1] == expected
    assert results["bob"][0][1]["좋음"] == (1, 1, 1.0)
    assert results["bob"][0][1]["나쁨"] == (0, 0, None)


def test_bom_quoted_identity_and_no_mutation() -> None:
    row = record(file='a,b"c.pdf', labeler=" alice ")
    before = row.copy()
    text = sheet(row)
    assert compare_sheet("\ufeff" + text, "score", [0], comparison="<=")["alice"][0][1]["좋음"] == (
        1,
        1,
        1.0,
    )
    assert row == before


@pytest.mark.parametrize("page,order", [("1", "1"), ("01", "001")])
def test_duplicate_identifiers_rejected(page: str, order: str) -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        compare_sheet(sheet(record(), record(page=page, order=order)), "score", [0], comparison="<")


@pytest.mark.parametrize(
    "changes",
    [
        {"label": ""},
        {"label": "좋음 "},
        {"label": "보통"},
        {"labeler": " "},
        {"file": ""},
        {"page": "0"},
        {"order": "-1"},
        {"page": "1.0"},
        {"order": "１"},
        {"extract_version": "0"},
        {"answer_len": "-1"},
        {"hangul": "2"},
        {"exposure_count": "true"},
        {"page_target": "1.5"},
        {"score": "nan"},
        {"page_survival": "inf"},
        {"size_signal": "-0.1"},
        {"bold_signal": "1.1"},
        {"heading_signal": "=1+1"},
        {"frequency_signal": ""},
    ],
)
def test_invalid_cells_even_outside_selected_metric(changes: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        compare_sheet(sheet(record(**changes)), "score", [0.5], comparison="<")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "file,label\na,좋음\n",
        sheet() + '"unterminated',
        sheet() + "too,few\n",
        sheet() + "," * 20 + "\n",
    ],
)
def test_invalid_structure(text: str) -> None:
    with pytest.raises((ValueError, csv.Error)):
        compare_sheet(text, "score", [0.5], comparison="<")


def test_duplicate_header() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        compare_sheet(sheet(header=(*COLUMNS, "score")), "score", [0], comparison="<")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True])
def test_invalid_candidates_on_empty_sheet(value: float) -> None:
    with pytest.raises(ValueError):
        compare_sheet(sheet(), "score", [value], comparison="<")


@pytest.mark.parametrize("metric,comparison", [("file", "<"), ("score", "==")])
def test_invalid_metric_or_operator(metric: str, comparison: str) -> None:
    with pytest.raises(ValueError):
        compare_sheet(sheet(), metric, [0], comparison=comparison)


@pytest.mark.parametrize("comparison,removed", [("<", 0), ("<=", 1), (">", 0), (">=", 1)])
def test_exact_threshold_boundary(comparison: str, removed: int) -> None:
    result = compare_sheet(sheet(record()), "score", [0], comparison=comparison)
    assert result["alice"][0][1]["좋음"] == (removed, 1, float(removed))


@pytest.mark.parametrize("metric", METRICS)
def test_generated_sheet_compatibility_for_all_metrics(metric: str) -> None:
    rows = pdf_rows(Path(__file__).parent / "fixtures" / "sample.pdf")
    converted = [{key: str(value) for key, value in row.items()} for row in rows]
    for row in converted:
        row.update(label="좋음", labeler="alice")
    result = compare_sheet(sheet(*converted), metric, [0], comparison=">=")
    assert result["alice"][0][1]["좋음"] == (len(rows), len(rows), 1.0)


def test_cli_bom_output_and_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "sheet.csv"
    path.write_text(sheet(record("나쁨")), encoding="utf-8-sig")
    before = path.read_bytes()
    main([str(path), "--metric", "score", "--comparison", "<=", "--thresholds", "0", "1"])
    output = capsys.readouterr().out
    assert output.splitlines() == [
        "labeler\tmetric\toperator\tthreshold\tlabel\tremoved\ttotal\trate",
        "alice\tscore\t<=\t0\t좋음\t0\t0\tN/A",
        "alice\tscore\t<=\t0\t애매\t0\t0\tN/A",
        "alice\tscore\t<=\t0\t나쁨\t1\t1\t1.0000",
        "alice\tscore\t<=\t1\t좋음\t0\t0\tN/A",
        "alice\tscore\t<=\t1\t애매\t0\t0\tN/A",
        "alice\tscore\t<=\t1\t나쁨\t1\t1\t1.0000",
    ]
    assert path.read_bytes() == before


def test_empty_sheet_and_candidates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert compare_sheet(sheet(), "score", [0], comparison="<") == {}
    assert compare_sheet(sheet(record()), "score", [], comparison="<") == {"alice": []}
    path = tmp_path / "empty.csv"
    path.write_text(sheet(), encoding="utf-8")
    main([str(path), "--metric", "score", "--comparison", "<", "--thresholds", "0"])
    assert "No labeled rows." in capsys.readouterr().out


@pytest.mark.parametrize("contents", [None, "invalid", sheet(record(label=""))])
def test_cli_error_without_partial_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], contents: str | None
) -> None:
    path = tmp_path / "sheet.csv"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    with pytest.raises(SystemExit) as captured:
        main([str(path), "--metric", "score", "--comparison", "<", "--thresholds", "0"])
    assert captured.value.code == 2
    assert capsys.readouterr().out == ""
