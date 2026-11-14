import csv
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from cloze.cli.__main__ import main as cli_main
from cloze.cli.label_sheet import COLUMNS, DENSITY, SEED, check_rows, main

FIXTURES = Path(__file__).parent / "fixtures"
PDFS = ("sample.pdf", "twoup.pdf")


@pytest.fixture
def samples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "samples"
    folder.mkdir()
    for name in PDFS:
        shutil.copy(FIXTURES / name, folder / name)
    # 작업 폴더의 .env가 끼어들지 않게 빈 폴더에서 돌린다.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLOZE_SAMPLES_DIR", str(folder))
    return folder


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def _selected_count(pdf: Path, out: Path) -> int:
    """cli select가 같은 seed·density로 고른 칸 수."""
    cli_main(["select", str(pdf), "--out", str(out), "--seed", str(SEED)])
    pages = [json.loads(path.read_text("utf-8")) for path in out.glob("page_*.json")]
    return sum(len(page["selection"]["spans"]) for page in pages)


def test_sheet_has_fixed_columns_and_one_row_per_blank(samples: Path, tmp_path: Path) -> None:
    assert DENSITY == 5.0
    main(["make", "--out", "sheet.csv"])
    header, rows = _read(tmp_path / "sheet.csv")
    assert header == list(COLUMNS)
    expected = {name: _selected_count(samples / name, tmp_path / name) for name in PDFS}
    assert all(expected.values())
    assert Counter(row["file"] for row in rows) == expected
    assert all(row["label"] == "" and row["labeler"] == "" for row in rows)


def test_rows_are_ordered_per_page_like_mask(samples: Path, tmp_path: Path) -> None:
    main(["make", "--out", "sheet.csv"])
    _, rows = _read(tmp_path / "sheet.csv")
    first = [row for row in rows if row["file"] == "sample.pdf" and row["page"] == "1"]
    assert [row["order"] for row in first] == [str(n) for n in range(1, len(first) + 1)]
    assert all(row["answer"] and float(row["page_survival"]) <= 1 for row in rows)


def test_same_samples_give_same_sheet(samples: Path, tmp_path: Path) -> None:
    main(["make", "--out", "a.csv"])
    main(["make", "--out", "b.csv"])
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()


def test_refuses_to_overwrite_without_force(samples: Path, tmp_path: Path) -> None:
    (tmp_path / "sheet.csv").write_text("labels in progress", encoding="utf-8")
    with pytest.raises(SystemExit, match="--force"):
        main(["make", "--out", "sheet.csv"])
    main(["make", "--out", "sheet.csv", "--force"])
    assert _read(tmp_path / "sheet.csv")[0] == list(COLUMNS)


def test_missing_samples_dir_prints_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLOZE_SAMPLES_DIR", raising=False)
    with pytest.raises(SystemExit, match="CLOZE_SAMPLES_DIR"):
        main(["make", "--out", "sheet.csv"])
    assert not (tmp_path / "sheet.csv").exists()


def test_check_accepts_three_labels_and_blank_rows() -> None:
    rows = [
        {"label": "좋음", "labeler": "a"},
        {"label": "애매", "labeler": "a"},
        {"label": "나쁨", "labeler": "a"},
        {"label": "", "labeler": ""},
    ]
    assert check_rows(rows) == []


def test_check_reports_unknown_label_and_missing_labeler() -> None:
    problems = check_rows([{"label": "보통", "labeler": "a"}, {"label": "좋음", "labeler": " "}])
    assert problems == [
        "2줄: label '보통'은 좋음 / 애매 / 나쁨 중 하나가 아니다",
        "3줄: labeler가 비어 있다",
    ]
