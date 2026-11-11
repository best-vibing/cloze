"""SDK 오류의 현재 분류와 설정별 동시성에 대한 오프라인 회귀 테스트."""

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
FAKE_API_KEY = "fake-api-key-regression-only"
FAKE_RESPONSE_BODY = "fake-external-response-body-regression-only"


@pytest.fixture(autouse=True)
def isolated_sdk_and_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """로컬 설정과 실제 SDK 및 네트워크 접근을 차단한다."""
    monkeypatch.chdir(tmp_path)
    for name in ("API_KEY", "MODEL", "TIMEOUT_S", "MAX_CONCURRENCY"):
        monkeypatch.delenv(f"CLOZE_GEMINI_{name}", raising=False)
    monkeypatch.delenv("CLOZE_SAMPLES_DIR", raising=False)
    blocked = MagicMock(side_effect=AssertionError("Unexpected network or SDK access"))
    monkeypatch.setattr(genai, "Client", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def sdk_client(monkeypatch: pytest.MonkeyPatch, sdk: MagicMock) -> GeminiClient:
    """실제 어댑터를 가짜 SDK와 가짜 키로 구성한다."""
    sdk.aio.aclose = AsyncMock()
    monkeypatch.setattr(genai, "Client", MagicMock(return_value=sdk))
    return GeminiClient(Settings(gemini_api_key=SecretStr(FAKE_API_KEY)))


def injected_sdk_error(kind: str, private: str) -> ValueError:
    """발생 단계를 재현하지 않고 주입할 ValueError 계열 예외를 만든다."""
    if kind == "json":
        return json.JSONDecodeError(private, private, 0)
    if kind == "pydantic":
        return ValidationError.from_exception_data(
            "SDK response",
            [{"type": "int_parsing", "loc": ("value",), "input": private}],
        )
    return ValueError(private)


@pytest.mark.parametrize("private", [FAKE_API_KEY, FAKE_RESPONSE_BODY], ids=["api-key", "body"])
@pytest.mark.parametrize("kind", ["value", "json", "pydantic"])
def test_injected_generate_content_value_errors_follow_current_mapping(
    kind: str, private: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """주입한 예외의 현재 분류만 확인하며 SDK 발생 단계는 입증하지 않는다.

    SDK 요청 검증에서도 ValueError가 가능하므로 응답 디코딩 계약으로 해석하지 않는다.
    요청 오류의 오분류 가능성은 운영 코드 PR #64에서 별도로 검토한다.
    """
    sdk = MagicMock()
    send = AsyncMock(side_effect=injected_sdk_error(kind, private))
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
                assert_suppressed_private_context(captured.value, private)
                send.assert_awaited_once()
                delay.assert_not_awaited()
            finally:
                await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("private", [FAKE_API_KEY, FAKE_RESPONSE_BODY], ids=["api-key", "body"])
@pytest.mark.parametrize("kind", ["value", "pydantic"])
def test_request_configuration_errors_are_permanent(
    kind: str, private: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """요청 구성 단계의 ValueError 계열은 전송 없이 영구 오류가 된다."""
    sdk = MagicMock()
    sdk.aio.models.generate_content = AsyncMock()
    client = sdk_client(monkeypatch, sdk)
    config = MagicMock(side_effect=injected_sdk_error(kind, private))
    monkeypatch.setattr(types, "GenerateContentConfig", config)
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_retry_delay", delay)

    async def scenario() -> None:
        """설정 생성 실패의 일반 traceback과 재시도 여부를 확인한다."""
        async with asyncio.timeout(3):
            try:
                with pytest.raises(LLMPermanentError) as captured:
                    await client.ask_llm("prompt", SCHEMA)
                assert_suppressed_private_context(captured.value, private)
                config.assert_called_once()
                sdk.aio.models.generate_content.assert_not_awaited()
                delay.assert_not_awaited()
            finally:
                await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_explicit_concurrency_setting_limits_active_transports(limit: int) -> None:
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

            try:
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
            finally:
                await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid"])
def test_invalid_concurrency_environment_is_rejected(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """양의 정수 이외 환경 설정은 기존 Pydantic 규칙으로 거절된다."""
    monkeypatch.setenv("CLOZE_GEMINI_MAX_CONCURRENCY", value)
    with pytest.raises(ValidationError) as captured:
        Settings()
    assert captured.value.errors()[0]["loc"] == ("gemini_max_concurrency",)


def assert_suppressed_private_context(error: Exception, private: str) -> None:
    """메시지와 일반 traceback은 숨기지만 원본 context는 남음을 확인한다."""
    assert private not in str(error)
    assert private not in "".join(traceback.format_exception(error))
    assert_retained_private_context(error, private)


def assert_retained_private_context(error: Exception, private: str) -> None:
    """from None이 원본 예외의 비밀값을 삭제하지는 않음을 확인한다."""
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert error.__context__ is not None
    assert private in str(error.__context__)
