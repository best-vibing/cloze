from typing import Any

import pytest

from cloze.core.quality_calibration import (
    bad_removal_rate,
    compare_thresholds,
    evaluate_removals,
    good_removal_rate,
    parse_label_csv,
)


def parse(text: str, **kwargs: Any) -> Any:
    return parse_label_csv(
        text, key_columns=["id"], label_column="label", value_column="value", **kwargs
    )


def test_mixed_counts_rates_and_immutability() -> None:
    keys = [(str(i),) for i in range(7)]
    labels = ["나쁨", "좋음", "애매", "나쁨", "좋음", "나쁨", "좋음"]
    removed = [True, True, True, True, False, False, False]
    before = (keys.copy(), labels.copy(), removed.copy())
    expected = {"좋음": (1, 3, 1 / 3), "나쁨": (2, 3, 2 / 3), "애매": (1, 1, 1.0)}
    assert evaluate_removals(keys, labels, removed) == expected
    assert bad_removal_rate(keys, labels, removed) == 2 / 3
    assert good_removal_rate(keys, labels, removed) == 1 / 3
    assert evaluate_removals(keys, labels, removed) == expected
    assert (keys, labels, removed) == before


@pytest.mark.parametrize("label", ["좋음", "나쁨", "애매"])
def test_single_label_and_zero_denominators(label: str) -> None:
    result = evaluate_removals([("a",), ("b",)], [label, label], [False, True])
    assert result[label] == (1, 2, 0.5)
    assert all(result[other] == (0, 0, None) for other in result if other != label)


def test_empty_inputs() -> None:
    assert evaluate_removals([], [], []) == dict.fromkeys(["좋음", "애매", "나쁨"], (0, 0, None))
    assert bad_removal_rate([], [], []) is None
    assert good_removal_rate([], [], []) is None
    assert parse("id,label,value\n") == []
    assert compare_thresholds([], [1], comparison="<")[0][1]["나쁨"] == (0, 0, None)
    assert compare_thresholds([], [], comparison="<") == []


@pytest.mark.parametrize(
    ("keys", "labels", "removed"),
    [
        ([("a",)], [], [True]),
        ([], ["좋음"], [True]),
        ([("a",)], ["좋음"], []),
        ([("a",)], ["unknown"], [True]),
        ([("a",)], ["좋음"], [1]),
        ([("a",)], ["좋음"], ["False"]),
        ([("a",)], ["좋음"], [None]),
        ([("a",), ("a",)], ["좋음", "나쁨"], [True, False]),
        ([()], ["좋음"], [True]),
        ([(" ",)], ["좋음"], [True]),
        (["a"], ["좋음"], [True]),
        ([(1,)], ["좋음"], [True]),
        ([(None,)], ["좋음"], [True]),
    ],
)
def test_invalid_evaluation(keys: Any, labels: Any, removed: Any) -> None:
    with pytest.raises(ValueError):
        evaluate_removals(keys, labels, removed)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n",
        "id,label\na,좋음\n",
        "id,label,value,value\na,좋음,1,2\n",
        "id,,value\na,좋음,1\n",
        "id,label,value\na,좋음\n",
        "id,label,value\na,좋음,1,extra\n",
        "id,label,value\n,좋음,1\n",
        "id,label,value\na, ,1\n",
        "id,label,value\na,좋음,\n",
        "id,label,value\na,unknown,1\n",
        "id,label,value\na,좋음,1\na,나쁨,2\n",
        "id,label,value,rater\na,좋음,1,alice\na,좋음,1,bob\n",
        'id,label,value\n"unterminated,좋음,1',
    ],
)
def test_invalid_csv_structure_and_values(text: str) -> None:
    with pytest.raises(ValueError):
        parse(text)


@pytest.mark.parametrize("value", ["NaN", "inf", "-inf", "1e9999", "word", "=1+1"])
def test_invalid_csv_numbers(value: str) -> None:
    with pytest.raises(ValueError, match="numeric"):
        parse(f"id,label,value\na,좋음,{value}\n")


def test_csv_custom_columns_quoted_text_and_inert_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.open", lambda *a, **kw: pytest.fail("Unexpected file access"))
    monkeypatch.setattr("io.open", lambda *a, **kw: pytest.fail("Unexpected path access"))
    text = 'document,blank,rating,score,note\n"a,b\nc",1,나쁨,-2,"=HYPERLINK()"\n'
    result = parse_label_csv(
        text, key_columns=["document", "blank"], label_column="rating", value_column="score"
    )
    assert result == [(("a,b\nc", "1"), "나쁨", -2.0)]
    for payload in ["../../secret", "https://invalid.example", "=1+1", "__import__('os')"]:
        assert parse(f"id,label,value\n{payload},좋음,1\n")[0][0] == (payload,)
    assert text.startswith("document,blank,rating,score,note")


def test_csv_range_and_configuration() -> None:
    assert parse("id,label,value\na,좋음,-1\nb,나쁨,1\n", value_range=(-1, 1)) == [
        (("a",), "좋음", -1.0),
        (("b",), "나쁨", 1.0),
    ]
    for bounds in [(0, 1), (2, 1), (float("nan"), 1), (0, float("inf"))]:
        with pytest.raises(ValueError):
            parse("id,label,value\na,좋음,-1\n", value_range=bounds)
    for keys in [[], ["id", "id"], ["label"]]:
        with pytest.raises(ValueError):
            parse_label_csv(
                "id,label,value\n", key_columns=keys, label_column="label", value_column="value"
            )


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("<", 1, 0),
        ("<=", 1, 1),
        (">", 1, 0),
        (">=", 1, 1),
        ("<", 0, 1),
        ("<=", 2, 0),
        (">", 2, 1),
        (">=", 0, 0),
    ],
)
def test_threshold_boundaries(op: str, value: float, expected: int) -> None:
    result = compare_thresholds([(("a",), "나쁨", value)], [1.0], comparison=op)
    assert result[0][1]["나쁨"] == (expected, 1, float(expected))


def test_candidates_order_counts_determinism_and_immutability() -> None:
    samples = [(("a",), "나쁨", 0.2), (("b",), "좋음", 0.8), (("c",), "애매", 0.4)]
    thresholds = [0.9, 0.1, 0.5, 0.5]
    before = (samples.copy(), thresholds.copy())
    result = compare_thresholds(samples, thresholds, comparison="<")
    assert [item[0] for item in result] == thresholds
    assert [item[1]["나쁨"] for item in result] == [(1, 1, 1.0), (0, 1, 0.0)] + [
        (1, 1, 1.0),
        (1, 1, 1.0),
    ]
    assert [item[1]["좋음"][0] for item in result] == [1, 0, 0, 0]
    assert compare_thresholds(samples, thresholds, comparison="<") == result
    assert (samples, thresholds) == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "1"])
def test_invalid_threshold_numbers(value: Any) -> None:
    with pytest.raises(ValueError):
        compare_thresholds([], [value], comparison="<")
    with pytest.raises(ValueError):
        compare_thresholds([(("a",), "좋음", value)], [], comparison="<")


def test_comparison_validates_even_without_candidates() -> None:
    with pytest.raises(ValueError, match="Comparison"):
        compare_thresholds([], [], comparison="==")
    with pytest.raises(ValueError, match="Duplicate"):
        compare_thresholds([(("a",), "좋음", 1), (("a",), "나쁨", 2)], [], comparison="<")
