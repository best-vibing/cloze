"""429 대기·한도 구분(이슈 #120). 응답 모양은 실제 무료 등급 429에서 키 등을 지운 것이다."""

import asyncio
import traceback
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import errors

from cloze.config import Settings
from cloze.core.errors import LLMPermanentError, LLMQuotaExhausted, LLMTransientError
from cloze.infra import gemini
from cloze.infra.gemini import GeminiClient, JSONObject, LLMAttempt

pytestmark = pytest.mark.usefixtures("isolated_environment")

SCHEMA: JSONObject = {"type": "object", "required": ["ok"]}
PRIVATE = "private-quota-body"
PER_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
PER_DAY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """실제로 기다리지 않고, 무작위 지연을 0으로 고정한다."""
    monkeypatch.setattr(gemini, "_retry_delay", AsyncMock())
    monkeypatch.setattr(gemini, "_jitter", lambda: 0.0)
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_rate_limit_delay", delay)
    return delay


def rate_limit(quota_id: str | None = None, retry_delay: str | None = None) -> errors.APIError:
    """실측한 429 응답 모양(google.rpc.Help·QuotaFailure·RetryInfo)을 만든다."""
    details: list[dict[str, Any]] = [
        {"@type": "type.googleapis.com/google.rpc.Help", "links": [{"url": PRIVATE}]}
    ]
    if quota_id is not None:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": quota_id, "description": PRIVATE}],
            }
        )
    if retry_delay is not None:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    body = {"code": 429, "message": PRIVATE, "status": "RESOURCE_EXHAUSTED", "details": details}
    return errors.APIError(429, {"error": body})


def run(
    responses: list[Any], settings: Settings | None = None
) -> tuple[MagicMock, list[LLMAttempt], JSONObject | Exception]:
    transport = MagicMock()
    transport.generate = AsyncMock(side_effect=responses)
    transport.aclose = AsyncMock()
    records: list[LLMAttempt] = []
    client = GeminiClient(settings or Settings(), transport=transport)
    try:
        result: JSONObject | Exception = asyncio.run(
            client.ask_llm("prompt", SCHEMA, on_attempt=records.append)
        )
    except Exception as error:  # noqa: BLE001 - 테스트가 종류를 직접 확인한다.
        result = error
    return transport, records, result


def waits(delay: AsyncMock) -> list[float]:
    return [call.args[0] for call in delay.await_args_list]


def test_retry_delay_is_used_then_success(no_sleep: AsyncMock) -> None:
    """RetryInfo가 있으면 그 시간만큼 기다리고 다시 보낸다."""
    _, records, result = run([rate_limit(PER_MINUTE, "59s"), '{"ok":true}'])
    assert result == {"ok": True}
    assert waits(no_sleep) == [59.0]
    assert [record.outcome for record in records] == ["transient", "ok"]
    gemini._retry_delay.assert_not_awaited()  # type: ignore[attr-defined]


def test_backoff_grows_within_cap_then_transient(no_sleep: AsyncMock) -> None:
    """대기 시간이 없는 분당 한도 429는 기본값부터 두 배씩 늘리고, 끝내 일시적 오류다."""
    transport, records, result = run([rate_limit(PER_MINUTE)] * 5)
    assert isinstance(result, LLMTransientError)
    assert not isinstance(result, LLMQuotaExhausted)
    assert waits(no_sleep) == [4.0, 8.0, 16.0, 32.0]
    assert sum(waits(no_sleep)) <= Settings().gemini_rate_limit_max_wait_s
    assert transport.generate.await_count == 5
    assert [record.outcome for record in records] == ["transient"] * 5


def test_total_wait_cap_stops_early(no_sleep: AsyncMock) -> None:
    """다음 대기가 합계 상한을 넘으면 기다리지 않고 끝낸다."""
    settings = Settings(gemini_rate_limit_max_wait_s=10)
    transport, _, result = run([rate_limit(PER_MINUTE)] * 5, settings)
    assert isinstance(result, LLMTransientError)
    assert waits(no_sleep) == [4.0]
    assert transport.generate.await_count == 2


@pytest.mark.parametrize(("quota_id", "expected"), [(PER_MINUTE, "transient"), (None, "quota")])
def test_retry_delay_longer_than_cap_is_not_waited(
    no_sleep: AsyncMock, quota_id: str | None, expected: str
) -> None:
    """상한보다 긴 retryDelay는 기다리지 않는다. 모르는 한도면 하루 한도로 본다."""
    transport, _, result = run([rate_limit(quota_id, "3600s")])
    no_sleep.assert_not_awaited()
    assert transport.generate.await_count == 1
    assert isinstance(result, LLMQuotaExhausted) == (expected == "quota")
    assert isinstance(result, LLMTransientError) == (expected == "transient")


def test_daily_quota_is_not_retried(no_sleep: AsyncMock) -> None:
    """하루 한도는 기다려도 풀리지 않으므로 바로 영구 오류 계열로 올린다."""
    transport, records, result = run([rate_limit(PER_DAY, "30s")])
    assert isinstance(result, LLMQuotaExhausted)
    assert isinstance(result, LLMPermanentError)
    assert transport.generate.await_count == 1
    assert [record.outcome for record in records] == ["quota"]
    no_sleep.assert_not_awaited()


def test_unknown_429_persisting_is_quota(no_sleep: AsyncMock) -> None:
    """한도 종류가 없는 429는 분당 한도처럼 기다리다가, 계속되면 하루 한도로 본다."""
    transport, records, result = run([errors.APIError(429, {})] * 5)
    assert isinstance(result, LLMQuotaExhausted)
    assert waits(no_sleep) == [4.0, 8.0, 16.0, 32.0]
    assert [record.outcome for record in records] == ["transient"] * 5


def test_unknown_429_then_success(no_sleep: AsyncMock) -> None:
    _, _, result = run([errors.APIError(429, {"error": "not-a-dict"}), '{"ok":true}'])
    assert result == {"ok": True}
    assert waits(no_sleep) == [4.0]


def test_rate_limit_and_transient_counts_are_separate(no_sleep: AsyncMock) -> None:
    """429와 타임아웃이 섞여도 타임아웃은 기존처럼 1초 백오프를 쓴다."""
    _, _, result = run([rate_limit(PER_MINUTE, "1s"), TimeoutError(), '{"ok":true}'])
    assert result == {"ok": True}
    assert waits(no_sleep) == [1.0]
    gemini._retry_delay.assert_awaited_once_with(0)  # type: ignore[attr-defined]


@pytest.mark.parametrize("error", [rate_limit(PER_DAY), rate_limit(PER_MINUTE)])
def test_error_message_hides_response_body(error: errors.APIError) -> None:
    settings = Settings(gemini_rate_limit_attempts=1)
    _, _, result = run([error], settings)
    assert isinstance(result, Exception)
    rendered = "".join(traceback.format_exception(result))
    assert PRIVATE not in rendered
    assert "RESOURCE_EXHAUSTED" not in rendered


def test_wait_happens_outside_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 대기 중에는 동시 호출 자리를 반납한다."""
    client: GeminiClient | None = None
    free: list[bool] = []

    async def check(_: float) -> None:
        assert client is not None
        free.append(not client._semaphore.locked())

    monkeypatch.setattr(gemini, "_rate_limit_delay", check)
    transport = MagicMock()
    transport.generate = AsyncMock(side_effect=[rate_limit(PER_MINUTE, "2s"), '{"ok":true}'])
    client = GeminiClient(Settings(gemini_max_concurrency=1), transport=transport)
    assert asyncio.run(client.ask_llm("prompt", SCHEMA)) == {"ok": True}
    assert free == [True]


def test_count_tokens_shares_rate_limit_policy(no_sleep: AsyncMock) -> None:
    transport = MagicMock()
    transport.count_tokens = AsyncMock(side_effect=[rate_limit(PER_MINUTE, "5s"), 9])
    client = GeminiClient(Settings(), transport=transport)
    assert asyncio.run(client.count_tokens("prompt")) == 9
    assert waits(no_sleep) == [5.0]
    transport.count_tokens.side_effect = [rate_limit(PER_DAY)]
    with pytest.raises(LLMQuotaExhausted):
        asyncio.run(client.count_tokens("prompt"))


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOZE_GEMINI_RATE_LIMIT_ATTEMPTS", "3")
    monkeypatch.setenv("CLOZE_GEMINI_RATE_LIMIT_BASE_S", "1.5")
    monkeypatch.setenv("CLOZE_GEMINI_RATE_LIMIT_MAX_WAIT_S", "30")
    settings = Settings()
    assert settings.gemini_rate_limit_attempts == 3
    assert settings.gemini_rate_limit_base_s == 1.5
    assert settings.gemini_rate_limit_max_wait_s == 30
