import csv
import json
import shutil
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from cloze.cli import pipeline as pipeline_cli
from cloze.cli.__main__ import main as cli_main
from cloze.cli.label_sheet import COLUMNS, DENSITY, SEED, _page_rows, check_rows, main, read_sheet
from cloze.core.extract import EXTRACT_VERSION
from cloze.core.gate import apply_gate
from cloze.core.models import PageCandidates, PageInfo, PageTokens, Policy, Selection, Slot, Token
from cloze.core.pipeline import PreparedPage
from cloze.core.select.rule import DocStats

FIXTURES = Path(__file__).parent / "fixtures"
PDFS = ("sample.pdf", "twoup.pdf")


def test_from_pipeline_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    out = tmp_path / "labels.csv"
    out.write_text("existing ratings", encoding="utf-8")
    with pytest.raises(SystemExit, match="--force"):
        main(
            [
                "from-pipeline",
                "--document",
                "sample.pdf",
                str(tmp_path),
                "--density",
                "5",
                "--out",
                str(out),
            ]
        )
    assert out.read_text(encoding="utf-8") == "existing ratings"


def test_from_pipeline_keeps_gemini_phrase_and_page_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page_info = PageInfo(page_no=1, width_pt=100, height_pt=100)
    tokens = [
        Token(
            id=index,
            text=text,
            line_id=0,
            font_size=12,
            fontname="Regular",
            bbox=(0.1 + index * 0.2, 0.1, 0.25 + index * 0.2, 0.2),
        )
        for index, text in enumerate(("Gradient", "Descent"))
    ]
    image = Image.new("RGB", (100, 100), "white")
    pages = [
        PreparedPage(PageTokens(page=page_info, tokens=tokens), image, True),
        PreparedPage(
            PageTokens(page=page_info.model_copy(update={"page_no": 2}), tokens=[]), image, False
        ),
    ]
    client = Mock(
        ask_llm=AsyncMock(return_value={"blanks": [{"token_ids": [0, 1], "type": "term"}]}),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(pipeline_cli, "GeminiClient", Mock(return_value=client))
    monkeypatch.setattr(pipeline_cli, "prepare", Mock(return_value=pages))
    pipeline_cli.run_pipeline(
        b"fixture",
        Policy(density=50, preset="concept", seed=SEED),
        tmp_path,
        rule_only=False,
        dump_stages=True,
    )
    out = tmp_path / "labels.csv"
    main(
        [
            "from-pipeline",
            "--document",
            "sample.pdf",
            str(tmp_path),
            "--out",
            str(out),
            "--density",
            "50",
        ]
    )
    rows, problems = read_sheet(out.read_text(encoding="utf-8-sig"), completed=False)
    assert problems == []
    (row,) = rows
    assert (row["answer"], row["order"], row["selection_source"]) == (
        "Gradient Descent",
        "1",
        "gemini",
    )
    assert (row["exposure_count"], row["page_target"], row["label"], row["labeler"]) == (
        "0",
        "1",
        "",
        "",
    )
    assert "gemini=1, rule=0, none=1" in capsys.readouterr().out
    client.ask_llm.assert_awaited_once()
    original = out.read_bytes()
    args = ["from-pipeline", "--document", "sample.pdf", str(tmp_path), "--out", str(out)]
    with pytest.raises(SystemExit, match="--density"):
        main([*args, "--density", "2", "--force"])
    stages = tmp_path / "page_001.stages.json"
    stages.rename(stages.with_suffix(".backup"))
    with pytest.raises(SystemExit, match="--dump-stages"):
        main([*args, "--density", "50", "--force"])
    assert out.read_bytes() == original


@pytest.mark.parametrize(("other_span", "exposure"), [([], 1), ([7], 1), ([6, 7], 0)])
def test_phrase_exposure_counts_same_answer_units(other_span: list[int], exposure: int) -> None:
    words = [
        ("Gradient", 0),
        ("Descent", 0),
        ("Gradient", 1),
        ("method", 1),
        ("Gradient", 2),
        ("Descent", 3),
        ("Gradient", 4),
        ("Descent", 4),
    ]
    tokens = [
        Token(
            id=index,
            text=word,
            line_id=line,
            bbox=(0.1 + index * 0.1, 0.1 + line * 0.1, 0.15 + index * 0.1, 0.15 + line * 0.1),
            font_size=12,
            fontname="Regular",
        )
        for index, (word, line) in enumerate(words)
    ]
    page = PageTokens(page=PageInfo(page_no=1, width_pt=100, height_pt=100), tokens=tokens)
    spans = [[0, 1], [3]]
    if other_span:
        spans.append(other_span)
    selection = Selection(spans=spans, slots=[Slot() for _ in spans], source="rule")

    rows = _page_rows("sample.pdf", page, selection, len(spans), DocStats(Counter(), frozenset()))

    assert rows[0]["answer"] == "Gradient Descent"
    assert rows[0]["answer_len"] == 15
    assert rows[0]["exposure_count"] == exposure
    assert str(rows[0]["context_after"]).startswith("Gradient [method]")
    assert rows[1]["exposure_count"] == 0

    if other_span:
        candidates = PageCandidates(page_no=1, candidate_ids=list(range(len(tokens))), excluded=[])
        gate = apply_gate(page, candidates, selection, Policy(preset="concept", seed=0))
        rejected = any(
            item.token_ids == [0, 1] and item.reason == "answer_exposed" for item in gate.rejected
        )
        assert rejected == bool(exposure)


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


def test_mixed_language_pdf_with_pages_without_blanks(samples: Path) -> None:
    for name in PDFS:
        (samples / name).unlink()
    shutil.copy(FIXTURES / "korean.pdf", samples)
    main(["make", "--out", "korean.csv"])
    _, rows = _read(samples.parent / "korean.csv")
    assert [(row["file"], row["page"], row["order"]) for row in rows] == [
        ("korean.pdf", "1", "1"),
    ]
    assert "gradient" in rows[0]["context_after"]
    assert rows[0]["extract_version"] == str(EXTRACT_VERSION)
    main(["check", "korean.csv"])


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


def test_pdf_directory_is_not_a_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "folder.pdf").mkdir()
    monkeypatch.setenv("CLOZE_SAMPLES_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="PDF가 없다"):
        main(["make", "--out", str(tmp_path / "sheet.csv")])


def test_corrupt_pdf_preserves_existing_sheet(samples: Path, tmp_path: Path) -> None:
    (samples / "broken.pdf").write_bytes(b"not a PDF")
    out = tmp_path / "sheet.csv"
    out.write_bytes(b"saved labels")
    with pytest.raises(SystemExit, match="broken[.]pdf"):
        main(["make", "--out", str(out), "--force"])
    assert out.read_bytes() == b"saved labels"


def test_empty_text_is_reported(
    samples: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("cloze.cli.label_sheet.extract_tokens", lambda _: [])
    main(["make", "--out", "empty.csv"])
    assert _read(samples.parent / "empty.csv") == (list(COLUMNS), [])
    assert "추출된 텍스트가 없다" in capsys.readouterr().err


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


def _check_file(tmp_path: Path, text: str) -> None:
    (tmp_path / "sheet.csv").write_text(text, encoding="utf-8-sig")
    main(["check", str(tmp_path / "sheet.csv")])


def test_check_reports_label_with_spaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _check_file(tmp_path, "label,labeler\n좋음 ,a\n좋음,a\n")
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert err == "2줄: label '좋음 ' 앞뒤에 공백이 있다\n"
    assert out == "2칸: 좋음 2, 애매 0, 나쁨 0, 빈 칸 0\n"


def test_check_reports_rows_with_wrong_column_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        _check_file(tmp_path, "label,labeler\n좋음\n좋음,a,b\n좋음,a\n")
    assert capsys.readouterr().err.splitlines() == [
        "2줄: 열 수가 머리글과 다르다",
        "3줄: 열 수가 머리글과 다르다",
    ]
