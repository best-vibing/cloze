"""Boundary and validation tests without database or Gemini access."""

from collections.abc import Callable
from typing import cast

import pytest

from cloze.core.usage_limits import (
    check_concurrent_set_limit,
    check_daily_page_limit,
    check_service_gemini_limit,
)


@pytest.mark.parametrize(
    ("used_pages", "requested_pages", "limit", "expected"),
    [
        (298, 1, 300, None),
        (299, 1, 300, None),
        (300, 1, 300, "daily_limit_exceeded"),
        (0, 1, 300, None),
        (0, 300, 300, None),
        (0, 301, 300, "daily_limit_exceeded"),
        (0, 1, 1, None),
        (999, 1, 1000, None),
        (1000, 1, 1000, "daily_limit_exceeded"),
    ],
)
def test_daily_page_limit(
    used_pages: int, requested_pages: int, limit: int, expected: str | None
) -> None:
    assert check_daily_page_limit(used_pages, requested_pages, limit) == expected


@pytest.mark.parametrize(
    ("active_sets", "limit", "expected"),
    [
        (0, 2, None),
        (1, 2, None),
        (2, 2, "too_many_running"),
        (3, 2, "too_many_running"),
        (0, 1, None),
        (19, 20, None),
        (20, 20, "too_many_running"),
    ],
)
def test_concurrent_set_limit(active_sets: int, limit: int, expected: str | None) -> None:
    assert check_concurrent_set_limit(active_sets, limit) == expected


@pytest.mark.parametrize(
    ("recorded_calls", "remaining_pages", "requested_pages", "limit", "expected"),
    [
        # These limits are test data, not service defaults.
        (6, 2, 1, 10, None),
        (6, 2, 2, 10, None),
        (7, 2, 2, 10, "service_limit_exceeded"),
        (6, 3, 2, 10, "service_limit_exceeded"),
        (6, 2, 3, 10, "service_limit_exceeded"),
        (0, 0, 1, 10, None),
        (0, 0, 1, 1, None),
        (0, 9, 1, 10, None),
        (9, 0, 1, 10, None),
        (0, 0, 11, 10, "service_limit_exceeded"),
    ],
)
def test_service_gemini_limit(
    recorded_calls: int,
    remaining_pages: int,
    requested_pages: int,
    limit: int,
    expected: str | None,
) -> None:
    assert (
        check_service_gemini_limit(recorded_calls, remaining_pages, requested_pages, limit)
        == expected
    )


# Each row changes exactly one public parameter and leaves the others valid.
VALIDATION_CASES: list[tuple[Callable[..., str | None], dict[str, int], str, int]] = (
    [
        (check_daily_page_limit, {"used_pages": 0, "requested_pages": 1, "limit": 300}, name, low)
        for name, low in [("used_pages", 0), ("requested_pages", 1), ("limit", 1)]
    ]
    + [
        (check_concurrent_set_limit, {"active_sets": 0, "limit": 2}, name, low)
        for name, low in [("active_sets", 0), ("limit", 1)]
    ]
    + [
        (
            check_service_gemini_limit,
            {"recorded_calls": 0, "remaining_pages": 0, "requested_pages": 1, "limit": 10},
            name,
            low,
        )
        for name, low in [
            ("recorded_calls", 0),
            ("remaining_pages", 0),
            ("requested_pages", 1),
            ("limit", 1),
        ]
    ]
)


@pytest.mark.parametrize(("check", "valid", "name", "minimum"), VALIDATION_CASES)
@pytest.mark.parametrize("invalid", [1.0, 0.5, "1", True, False, None, [], {}, (), {1}, 1j, b"1"])
def test_rejects_noninteger_inputs(
    check: Callable[..., str | None],
    valid: dict[str, int],
    name: str,
    minimum: int,
    invalid: object,
) -> None:
    # cast only informs mypy; the invalid runtime value reaches the validator unchanged.
    inputs = {**valid, name: cast(int, invalid)}
    with pytest.raises(TypeError, match=name):
        check(**inputs)


@pytest.mark.parametrize(("check", "valid", "name", "minimum"), VALIDATION_CASES)
@pytest.mark.parametrize("distance", [1, 2])
def test_rejects_out_of_range_inputs(
    check: Callable[..., str | None],
    valid: dict[str, int],
    name: str,
    minimum: int,
    distance: int,
) -> None:
    inputs = {**valid, name: minimum - distance}
    with pytest.raises(ValueError, match=name):
        check(**inputs)


@pytest.mark.parametrize("count", [2**63, 10**100])
def test_large_integer_boundaries(count: int) -> None:
    """Arbitrary precision daily totals preserve both boundary decisions."""
    assert check_daily_page_limit(count, count, 2 * count) is None
    assert check_daily_page_limit(count, count, 2 * count - 1) == "daily_limit_exceeded"


@pytest.mark.parametrize("count", [2**63, 10**100])
def test_large_concurrent_boundaries(count: int) -> None:
    """Large active counts include exactly one new set."""
    assert check_concurrent_set_limit(count, count + 1) is None
    assert check_concurrent_set_limit(count, count) == "too_many_running"


@pytest.mark.parametrize("count", [2**63, 10**100])
def test_large_service_boundaries(count: int) -> None:
    """All three service terms retain arbitrary precision."""
    assert check_service_gemini_limit(count, count, count, 3 * count) is None
    assert (
        check_service_gemini_limit(count, count, count, 3 * count - 1) == "service_limit_exceeded"
    )


def test_recorded_retries_are_not_collapsed_into_pages() -> None:
    """Two failed attempts and one success consume three recorded calls."""
    assert check_service_gemini_limit(3, 1, 1, 5) is None
    assert check_service_gemini_limit(3, 1, 1, 4) == "service_limit_exceeded"


@pytest.mark.parametrize(("check", "valid", "name", "minimum"), VALIDATION_CASES)
def test_validation_precedes_limit_exceeded(
    check: Callable[..., str | None], valid: dict[str, int], name: str, minimum: int
) -> None:
    """An exceeded total must not hide another parameter's invalid type."""
    inputs = {key: 10**100 for key in valid}
    inputs["limit"] = 1
    inputs[name] = cast(int, None)
    with pytest.raises(TypeError, match=name):
        check(**inputs)
