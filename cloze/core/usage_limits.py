"""Pure usage-limit checks over caller-provided aggregates and limits."""

from typing import Literal


def _validate_count(value: int, name: str, *, minimum: int) -> None:
    """Validate an integer count without coercion.

    Args:
        value: Count to validate.
        name: Parameter name used in error messages.
        minimum: Inclusive lower bound.

    Raises:
        TypeError: If value is not an integer or is a bool.
        ValueError: If value is below minimum.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, excluding bool")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def check_daily_page_limit(
    used_pages: int, requested_pages: int, limit: int
) -> Literal["daily_limit_exceeded"] | None:
    """Check the user's daily page total including the new request.

    Args:
        used_pages: Nonnegative sum for today's sets, including remakes and failures.
        requested_pages: Positive number of pages in the new request.
        limit: Positive daily page limit supplied by the caller.

    Returns:
        None if the total is within limit, otherwise "daily_limit_exceeded".

    Raises:
        TypeError: If any input is not an integer or is a bool.
        ValueError: If a count is negative, or requested_pages or limit is zero.
    """
    _validate_count(used_pages, "used_pages", minimum=0)
    _validate_count(requested_pages, "requested_pages", minimum=1)
    _validate_count(limit, "limit", minimum=1)
    return "daily_limit_exceeded" if used_pages + requested_pages > limit else None


def check_concurrent_set_limit(active_sets: int, limit: int) -> Literal["too_many_running"] | None:
    """Check whether one new set fits the user's concurrent set limit.

    Args:
        active_sets: Nonnegative count of existing queued or running sets.
        limit: Positive concurrent set limit supplied by the caller.

    Returns:
        None if one new set fits, otherwise "too_many_running".

    Raises:
        TypeError: If any input is not an integer or is a bool.
        ValueError: If active_sets is negative or limit is not positive.
    """
    _validate_count(active_sets, "active_sets", minimum=0)
    _validate_count(limit, "limit", minimum=1)
    return "too_many_running" if active_sets + 1 > limit else None


def check_service_gemini_limit(
    recorded_calls: int, remaining_pages: int, requested_pages: int, limit: int
) -> Literal["service_limit_exceeded"] | None:
    """Check recorded calls plus reserved pages and the new request.

    Args:
        recorded_calls: Nonnegative count of today's service-wide llm_calls.
        remaining_pages: Nonnegative remaining pages across queued or running sets.
        requested_pages: Positive number of pages in the new request.
        limit: Positive service call limit; no default is assumed.

    Returns:
        None if the sum is within limit, otherwise "service_limit_exceeded".

    Raises:
        TypeError: If any input is not an integer or is a bool.
        ValueError: If a count is negative, or requested_pages or limit is zero.
    """
    _validate_count(recorded_calls, "recorded_calls", minimum=0)
    _validate_count(remaining_pages, "remaining_pages", minimum=0)
    _validate_count(requested_pages, "requested_pages", minimum=1)
    _validate_count(limit, "limit", minimum=1)
    total = recorded_calls + remaining_pages + requested_pages
    return "service_limit_exceeded" if total > limit else None
