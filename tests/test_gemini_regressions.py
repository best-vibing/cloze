"""SDK 오류 분류와 재시도 수명 주기의 오프라인 회귀 테스트."""

import asyncio
import json
import socket
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from google import genai
from google.genai import types
from pydantic import SecretStr, ValidationError

from cloze.config import Settings
from cloze.core.errors import LLMInvalidResponse, LLMPermanentError
from cloze.infra import gemini
from cloze.infra.gemini import GeminiClient

SCHEMA: gemini.JSONObject = {"type": "object"}
PRIVATE = "fake-key-and-external-response-body"


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """로컬 설정과 실제 SDK 및 네트워크 접근을 차단한다."""
    monkeypatch.chdir(tmp_path)
    for name in ("API_KEY", "MODEL", "TIMEOUT_S", "MAX_CONCURRENCY"):
        monkeypatch.delenv(f"CLOZE_GEMINI_{name}", raising=False)
    blocked = MagicMock(side_effect=AssertionError("Unexpected network or SDK access"))
    monkeypatch.setattr(genai, "Client", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def sdk_client(monkeypatch: pytest.MonkeyPatch, sdk: MagicMock) -> GeminiClient:
    """실제 어댑터를 가짜 SDK와 가짜 키로 구성한다."""
    sdk.aio.aclose = AsyncMock()
    monkeypatch.setattr(genai, "Client", MagicMock(return_value=sdk))
    return GeminiClient(Settings(gemini_api_key=SecretStr(PRIVATE)))


def response_error(kind: str) -> ValueError:
    """SDK에서 발생할 수 있는 실제 예외 인스턴스를 만든다."""
    if kind == "json":
        return json.JSONDecodeError(PRIVATE, PRIVATE, 0)
    if kind == "pydantic":
        return ValidationError.from_exception_data(
            "SDK response",
            [{"type": "int_parsing", "loc": ("value",), "input": PRIVATE}],
        )
    return ValueError(PRIVATE)


@pytest.mark.parametrize("kind", ["value", "json", "pydantic"])
def test_sdk_response_errors_are_invalid_and_private(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전송 중 디코딩 오류는 설정 오류나 재시도로 바뀌지 않는다."""
    sdk = MagicMock()
    send = AsyncMock(side_effect=response_error(kind))
    sdk.aio.models.generate_content = send
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_retry_delay", delay)
    client = sdk_client(monkeypatch, sdk)

    async def scenario() -> None:
        """예외 분류, 공개 traceback과 재시도 횟수를 확인한다."""
        async with asyncio.timeout(3):
            try:
                with pytest.raises(LLMInvalidResponse) as captured:
                    await client.ask_llm("prompt", SCHEMA)
                assert not isinstance(captured.value, LLMPermanentError)
                assert PRIVATE not in "".join(traceback.format_exception(captured.value))
                send.assert_awaited_once()
                delay.assert_not_awaited()
            finally:
                await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["value", "pydantic"])
def test_request_configuration_errors_are_permanent(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """요청 구성 단계의 ValueError 계열은 전송 없이 영구 오류가 된다."""
    sdk = MagicMock()
    sdk.aio.models.generate_content = AsyncMock()
    client = sdk_client(monkeypatch, sdk)
    config = MagicMock(side_effect=response_error(kind))
    monkeypatch.setattr(types, "GenerateContentConfig", config)
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_retry_delay", delay)

    async def scenario() -> None:
        """설정 생성 실패가 외부 본문을 노출하거나 재시도하지 않는다."""
        async with asyncio.timeout(3):
            try:
                with pytest.raises(LLMPermanentError) as captured:
                    await client.ask_llm("prompt", SCHEMA)
                assert PRIVATE not in "".join(traceback.format_exception(captured.value))
                config.assert_called_once()
                sdk.aio.models.generate_content.assert_not_awaited()
                delay.assert_not_awaited()
            finally:
                await client.aclose()

    asyncio.run(scenario())


class BackoffGate:
    """이벤트로 백오프 진입과 해제를 제어한다."""

    def __init__(self) -> None:
        """현재 이벤트 루프에서 대기 신호를 만든다."""
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.attempts: list[int] = []

    async def delay(self, attempt: int) -> None:
        """실제 sleep 없이 재시도를 보류한다."""
        self.attempts.append(attempt)
        self.entered.set()
        await self.release.wait()


def test_backoff_releases_only_concurrency_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """상한 1에서도 첫 요청의 백오프 중 두 번째 전송이 완료된다."""

    async def scenario() -> None:
        """두 번째 요청 완료 후에만 첫 요청의 재시도를 허용한다."""
        async with asyncio.timeout(3):
            gate = BackoffGate()
            monkeypatch.setattr(gemini, "_retry_delay", gate.delay)
            transport = MagicMock()
            transport.generate = AsyncMock(side_effect=[TimeoutError(), "{}", "{}"])
            transport.aclose = AsyncMock()
            client = GeminiClient(Settings(gemini_max_concurrency=1), transport=transport)
            async with asyncio.TaskGroup() as group:
                first = group.create_task(client.ask_llm("first", SCHEMA))
                await gate.entered.wait()
                assert await client.ask_llm("second", SCHEMA) == {}
                assert not first.done()
                assert not gate.release.is_set()
                assert transport.generate.await_count == 2
                gate.release.set()
            assert first.result() == {}
            assert gate.attempts == [0]
            assert transport.generate.await_count == 3
            await client.aclose()

    asyncio.run(scenario())


def test_close_during_backoff_never_recreates_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """종료 후 재시도는 전송 연결을 재생성하지 않고 영구 오류로 끝난다."""

    async def scenario() -> None:
        """지연 중 종료를 완료한 다음 재시도와 새 요청을 검사한다."""
        async with asyncio.timeout(3):
            gate = BackoffGate()
            monkeypatch.setattr(gemini, "_retry_delay", gate.delay)
            transport = MagicMock()
            transport.generate = AsyncMock(side_effect=TimeoutError())
            transport.aclose = AsyncMock()
            factory = MagicMock(return_value=transport)
            monkeypatch.setattr(gemini, "_SDKTransport", factory)
            client = GeminiClient(Settings(gemini_max_concurrency=1))
            task = asyncio.create_task(client.ask_llm("first", SCHEMA))
            try:
                await gate.entered.wait()
                await client.aclose()
                transport.aclose.assert_awaited_once()
                assert not task.done()
                gate.release.set()
                with pytest.raises(LLMPermanentError, match="closed"):
                    await task
                with pytest.raises(LLMPermanentError, match="closed"):
                    await client.ask_llm("new", SCHEMA)
                factory.assert_called_once()
                transport.generate.assert_awaited_once()
                assert gate.attempts == [0]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_configured_concurrency_limit(limit: int) -> None:
    """설정된 수만 전송에 진입하고 남은 요청은 슬롯을 기다린다."""

    async def scenario() -> None:
        """모든 요청의 진입 시도와 전송 포화를 이벤트로 확인한다."""
        async with asyncio.timeout(3):
            release = asyncio.Event()
            saturated = asyncio.Event()
            attempted = [asyncio.Event() for _ in range(limit + 2)]
            active = 0
            peak = 0

            async def send(*args: object) -> str:
                """동시 전송 수를 기록하고 명시적 해제를 기다린다."""
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                if active == limit:
                    saturated.set()
                try:
                    await release.wait()
                    return "{}"
                finally:
                    active -= 1

            transport = MagicMock()
            transport.generate = AsyncMock(side_effect=send)
            transport.aclose = AsyncMock()
            client = GeminiClient(Settings(gemini_max_concurrency=limit), transport=transport)

            async def request(index: int) -> gemini.JSONObject:
                """요청 시작을 알린 뒤 중간 양보 없이 클라이언트에 진입한다."""
                attempted[index].set()
                return await client.ask_llm("prompt", SCHEMA)

            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(request(i)) for i in range(len(attempted))]
                await asyncio.gather(*(event.wait() for event in attempted))
                await saturated.wait()
                assert active == limit
                assert transport.generate.await_count == limit
                release.set()
            assert all(task.result() == {} for task in tasks)
            assert peak == limit
            assert transport.generate.await_count == len(attempted)
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid"])
def test_invalid_concurrency_setting(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """양의 정수 이외 환경 설정은 기존 Pydantic 규칙으로 거절된다."""
    monkeypatch.setenv("CLOZE_GEMINI_MAX_CONCURRENCY", value)
    with pytest.raises(ValidationError) as captured:
        Settings()
    assert captured.value.errors()[0]["loc"] == ("gemini_max_concurrency",)
