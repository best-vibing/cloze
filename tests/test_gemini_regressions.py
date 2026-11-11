"""SDK 오류의 현재 분류와 설정별 동시성에 대한 오프라인 회귀 테스트."""

import asyncio
import json
import traceback
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
                assert_detached_private_context(captured.value, private)
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
                assert_detached_private_context(captured.value, private)
                config.assert_called_once()
                sdk.aio.models.generate_content.assert_not_awaited()
                delay.assert_not_awaited()
            finally:
                await client.aclose()

    asyncio.run(scenario())


def direct_chain_log(error: BaseException) -> str:
    """?? ???? ???? cause? context ??? ?? ??? ????."""
    pending = [error]
    seen: set[int] = set()
    messages: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        messages.extend((str(current), repr(current)))
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return "\n".join(messages)


def assert_detached_private_context(error: Exception, private: str) -> None:
    """?? ??? ?? ?? ??? ????? ?? ?? ??? ????."""
    logged = direct_chain_log(error) + "".join(traceback.format_exception(error))
    for secret in (private, FAKE_API_KEY, FAKE_RESPONSE_BODY):
        assert secret not in logged
    assert error.__context__ is None
    assert error.__cause__ is None


@pytest.mark.parametrize("link", ["context", "cause"])
def test_chain_logger_detects_suppressed_secrets(link: str) -> None:
    """from None?? ?? ??? ???? ?? ??? ?? ??? ???."""
    error = LLMInvalidResponse("safe")
    private = ValueError(FAKE_API_KEY + FAKE_RESPONSE_BODY)
    setattr(error, f"__{link}__", private)
    error.__suppress_context__ = True
    logged = direct_chain_log(error)
    assert FAKE_API_KEY in logged
    assert FAKE_RESPONSE_BODY in logged
    with pytest.raises(AssertionError):
        assert_detached_private_context(error, FAKE_API_KEY)
