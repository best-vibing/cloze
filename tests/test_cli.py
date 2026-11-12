import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from cloze.cli.__main__ import main

SAMPLE = Path(__file__).parent / "fixtures" / "sample.pdf"


def _run(out: Path, *options: str) -> None:
    main(["select", str(SAMPLE), "--out", str(out), *options])


def _load(out: Path, page_no: int) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / f"page_{page_no:03d}.json").read_text("utf-8"))
    return data


def test_writes_one_json_per_page(tmp_path: Path) -> None:
    _run(tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "page_001.json",
        "page_002.json",
        "page_003.json",
    ]


def test_json_holds_tokens_candidates_reasons_and_selection(tmp_path: Path) -> None:
    _run(tmp_path, "--density", "10")
    page = _load(tmp_path, 1)
    assert page["schema_version"] == 1
    assert page["page"]["page_no"] == 1
    assert page["tokens"][0]["text"] == "Cloze"
    excluded = {item["id"]: item["reason"] for item in page["excluded"]}
    assert excluded[0] == "header_footer"
    assert set(excluded).isdisjoint(page["candidates"])
    assert len(excluded) + len(page["candidates"]) == len(page["tokens"])
    assert page["selection"]["source"] == "rule"
    assert all(span[0] in page["candidates"] for span in page["selection"]["spans"])


def test_terminal_table_shows_counts_and_words(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "page | candidates / selected | words"
    assert len(lines) == 4
    assert "Gradient" in lines[1]


def test_same_seed_writes_same_output(tmp_path: Path) -> None:
    _run(tmp_path / "a", "--seed", "7")
    _run(tmp_path / "b", "--seed", "7")
    assert _load(tmp_path / "a", 3) == _load(tmp_path / "b", 3)


def test_seed_changes_tied_page(tmp_path: Path) -> None:
    _run(tmp_path / "a", "--seed", "1", "--density", "10")
    _run(tmp_path / "b", "--seed", "2", "--density", "10")
    assert _load(tmp_path / "a", 3)["selection"] != _load(tmp_path / "b", 3)["selection"]


def test_select_requires_out(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["select", str(SAMPLE)])
    assert "--out" in capsys.readouterr().err


def _mask(out: Path, *options: str) -> None:
    main(["mask", str(SAMPLE), "--out", str(out), "--density", "10", *options])


def test_mask_writes_webp_and_json_per_page(tmp_path: Path) -> None:
    _mask(tmp_path)
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == [f"page_{no:03d}.{ext}" for no in (1, 2, 3) for ext in ("json", "webp")]
    with Image.open(tmp_path / "page_001.webp") as image:
        assert image.format == "WEBP"
        assert max(image.size) == 2000


def test_mask_json_lists_blanks_in_order(tmp_path: Path) -> None:
    _mask(tmp_path)
    page = _load(tmp_path, 1)
    assert page["extract_version"] == 1
    assert page["page"]["page_no"] == 1
    blanks = page["blanks"]
    assert [blank["order"] for blank in blanks] == list(range(1, len(blanks) + 1))
    assert "Gradient" in [blank["answer"] for blank in blanks]
    for blank in blanks:
        assert set(blank) == {"order", "answer", "bbox", "bg_color"}
        assert all(0.0 <= value <= 1.0 for value in blank["bbox"])
        assert blank["bg_color"] == "#ffffff"


def test_mask_debug_boxes_writes_png(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mask(tmp_path, "--debug-boxes")
    assert (tmp_path / "page_001.debug.png").exists()
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "page | blanks | answers"
    assert len(lines) == 4
