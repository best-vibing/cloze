"""실제 API 키나 네트워크를 사용하지 않는 Gemini 경계 테스트."""

import asyncio
import secrets
import socket
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google import genai
from google.genai import errors
from PIL import Image
from pydantic import SecretStr

from cloze.config import Settings
from cloze.core.errors import LLMInvalidResponse, LLMPermanentError, LLMTransientError
from cloze.infra import gemini
from cloze.infra.gemini import GeminiClient, JSONObject

SCHEMA: JSONObject = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


@dataclass
class FakeTransport:
    """순서대로 응답하거나 오류를 발생시키는 비동기 전송."""

    responses: deque[str | Exception] = field(default_factory=deque)
    calls: int = 0
    active: int = 0
    peak: int = 0
    closes: int = 0
    blocker: asyncio.Event | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    images: list[Image.Image | None] = field(default_factory=list)

    async def generate(self, prompt: str, schema: JSONObject, image: Image.Image | None) -> str:
        """호출 수를 기록하고 준비한 응답 또는 오류를 반환한다."""
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.images.append(image)
        self.entered.set()
        try:
            await self._wait()
            result = self.responses.popleft() if self.responses else '{"ok":true}'
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            self.active -= 1

    async def _wait(self) -> None:
        """테스트가 허용할 때까지 대기하거나 이벤트 루프에 양보한다."""
        if self.blocker is not None:
            await self.blocker.wait()
        await asyncio.sleep(0)

    async def aclose(self) -> None:
        """종료 호출 수를 기록한다."""
        self.closes += 1


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """가짜 전송 대신 실제 SDK를 만드는 실수를 막는다."""
    monkeypatch.setattr(genai, "Client", MagicMock(side_effect=AssertionError("live SDK")))
    monkeypatch.setattr(socket, "getaddrinfo", MagicMock(side_effect=AssertionError("network")))
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=AssertionError("network"))
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "CLOZE_GEMINI_API_KEY",
        "CLOZE_GEMINI_MODEL",
        "CLOZE_GEMINI_TIMEOUT_S",
        "CLOZE_GEMINI_MAX_CONCURRENCY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_valid_json_and_image_without_key() -> None:
    """키 없이 가짜 응답을 검증하고 PIL 이미지를 그대로 전달한다."""
    transport = FakeTransport()
    client = GeminiClient(Settings(), transport=transport)
    image = Image.new("RGB", (2, 2))
    result = asyncio.run(client.ask_llm("prompt", SCHEMA, image))
    assert result == {"ok": True}
    assert transport.images == [image]


@pytest.mark.parametrize("raw", ["invalid", "[]", '{"ok":"wrong"}', "{}", ""])
def test_invalid_responses_are_not_retried(raw: str) -> None:
    """파싱·객체·스키마 실패는 응답 오류이며 infra가 재시도하지 않는다."""
    transport = FakeTransport(deque([raw]))
    client = GeminiClient(transport=transport)
    with pytest.raises(LLMInvalidResponse):
        asyncio.run(client.ask_llm("prompt", SCHEMA))
    assert transport.calls == 1


@pytest.mark.parametrize("code", [408, 429, 500, 503, 599])
def test_transient_retry_then_success(code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """일시적 HTTP 오류 후 성공하며 정확히 한 번 기다린다."""
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_retry_delay", delay)
    transport = FakeTransport(deque([errors.APIError(code, {"message": "private"})]))
    result = asyncio.run(GeminiClient(transport=transport).ask_llm("prompt", SCHEMA))
    assert result == {"ok": True}
    assert transport.calls == 2
    delay.assert_awaited_once_with(0)


@pytest.mark.parametrize(
    "error", [TimeoutError("private"), httpx.ConnectError("private"), errors.APIError(429, {})]
)
def test_transport_errors_exhaust_three_attempts(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전송 오류는 총 세 번만 시도하고 안전한 오류로 끝난다."""
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_retry_delay", delay)
    transport = FakeTransport(deque([error, error, error]))
    with pytest.raises(LLMTransientError, match="3 attempts"):
        asyncio.run(GeminiClient(transport=transport).ask_llm("prompt", SCHEMA))
    assert transport.calls == 3
    assert delay.await_count == 2


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_error_is_private_and_not_retried(code: int) -> None:
    """인증·모델 오류의 비밀 본문을 표준 traceback에도 노출하지 않는다."""
    transport = FakeTransport(deque([errors.APIError(code, {"message": "secret-value"})]))
    with pytest.raises(LLMPermanentError) as captured:
        asyncio.run(GeminiClient(transport=transport).ask_llm("prompt", SCHEMA))
    assert "secret-value" not in "".join(traceback.format_exception(captured.value))
    assert transport.calls == 1


def test_missing_key_is_checked_lazily() -> None:
    """키 없는 클라이언트는 생성되지만 첫 실제 사용 때 명확히 실패한다."""
    client = GeminiClient(Settings())
    assert Settings().gemini_api_key is None
    with pytest.raises(LLMPermanentError, match="CLOZE_GEMINI_API_KEY is required"):
        asyncio.run(client.ask_llm("prompt", SCHEMA))


@pytest.mark.parametrize("contents", ["", " \n"])
def test_empty_key_is_rejected(contents: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """환경변수에서 읽은 빈 키와 공백 키는 SDK 생성 전에 거절한다."""
    monkeypatch.setenv("CLOZE_GEMINI_API_KEY", contents)
    sdk = MagicMock()
    monkeypatch.setattr(genai, "Client", sdk)
    with pytest.raises(LLMPermanentError, match="must not be empty"):
        gemini._SDKTransport(Settings())
    sdk.assert_not_called()


def test_key_error_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 초기화 오류에 키가 포함되어도 공개 오류와 traceback에서 숨긴다."""
    key = "private-placeholder"
    monkeypatch.setattr(genai, "Client", MagicMock(side_effect=ValueError(key)))
    client = GeminiClient(Settings(gemini_api_key=SecretStr(key)))
    with pytest.raises(LLMPermanentError) as captured:
        asyncio.run(client.ask_llm("prompt", SCHEMA))
    assert key not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize(
    ("schema", "message"),
    [({"type": 123}, "JSON Schema"), ({"$ref": "https://invalid.example/schema"}, "reference")],
)
def test_invalid_schema_does_not_send(schema: JSONObject, message: str) -> None:
    """잘못된 스키마와 외부 참조는 네트워크 전송 전에 거절한다."""
    transport = FakeTransport()
    with pytest.raises(LLMPermanentError, match=message):
        asyncio.run(GeminiClient(transport=transport).ask_llm("prompt", schema))
    assert transport.calls == 0


def test_concurrency_limit() -> None:
    """여러 요청을 같은 클라이언트에 보내도 활성 호출은 네 개 이하이다."""

    async def scenario() -> None:
        """동시에 대기하는 요청을 만들고 실행 중인 수를 확인한다."""
        transport = FakeTransport(blocker=asyncio.Event())
        client = GeminiClient(transport=transport)
        tasks = [asyncio.create_task(client.ask_llm("prompt", SCHEMA)) for _ in range(12)]
        await transport.entered.wait()
        await asyncio.sleep(0)
        assert transport.active == 4
        assert transport.blocker is not None
        transport.blocker.set()
        await asyncio.gather(*tasks)
        assert transport.peak == 4
        await client.aclose()

    asyncio.run(scenario())


def test_cancel_releases_slot() -> None:
    """호출 취소를 전파하고 세마포어와 전송의 활성 상태를 복구한다."""

    async def scenario() -> None:
        """대기 중 호출을 취소한 뒤 같은 클라이언트를 다시 사용한다."""
        transport = FakeTransport(blocker=asyncio.Event())
        client = GeminiClient(transport=transport)
        task = asyncio.create_task(client.ask_llm("prompt", SCHEMA))
        await transport.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.active == 0
        transport.blocker = None
        assert await client.ask_llm("prompt", SCHEMA) == {"ok": True}
        await client.aclose()

    asyncio.run(scenario())


def test_close_waits_and_is_idempotent() -> None:
    """종료는 진행 중 요청을 기다리고 중복 호출에도 한 번만 닫는다."""

    async def scenario() -> None:
        """활성 요청 중 종료를 시작한 뒤 완료와 새 요청 차단을 확인한다."""
        transport = FakeTransport(blocker=asyncio.Event())
        client = GeminiClient(transport=transport)
        task = asyncio.create_task(client.ask_llm("prompt", SCHEMA))
        await transport.entered.wait()
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        assert transport.blocker is not None
        transport.blocker.set()
        await task
        await closing
        await client.aclose()
        assert transport.closes == 1
        with pytest.raises(LLMPermanentError, match="closed"):
            await client.ask_llm("prompt", SCHEMA)

    asyncio.run(scenario())


def test_backoff_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """백오프가 지수 증가하고 무작위 지연이 더해지는지 확인한다."""
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(secrets, "randbelow", lambda limit: 500)
    asyncio.run(gemini._retry_delay(0))
    asyncio.run(gemini._retry_delay(1))
    assert [call.args[0] for call in sleep.await_args_list] == [1.5, 2.5]


def test_sdk_configuration_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 호출 인자와 단위 변환·내부 재시도 설정을 가짜 SDK로 확인한다."""
    fake = MagicMock()
    fake.aio.models.generate_content = AsyncMock(return_value=MagicMock(text='{"ok":true}'))
    fake.aio.aclose = AsyncMock()
    Path(".env").write_text("CLOZE_GEMINI_API_KEY=test-placeholder\n", encoding="utf-8")
    settings = Settings(gemini_model="test-model")
    assert isinstance(settings.gemini_api_key, SecretStr)
    sdk = MagicMock(return_value=fake)
    monkeypatch.setattr(genai, "Client", sdk)
    client = GeminiClient(settings)

    async def scenario() -> None:
        assert await client.ask_llm("prompt", SCHEMA) == {"ok": True}
        await client.aclose()

    asyncio.run(scenario())
    sdk.assert_called_once()
    assert sdk.call_args.kwargs["api_key"] == "test-placeholder"
    options = sdk.call_args.kwargs["http_options"]
    assert options.timeout == 60000
    assert options.retry_options.attempts == 1
    kwargs = fake.aio.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["contents"] == ["prompt"]
    assert kwargs["config"].response_json_schema == SCHEMA
    assert kwargs["config"].response_mime_type == "application/json"
    fake.aio.aclose.assert_awaited_once()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_timeout(timeout: float) -> None:
    """SDK를 생성하기 전에 잘못된 타임아웃을 거절한다."""
    with pytest.raises(LLMPermanentError, match="timeout"):
        gemini._http_options(Settings(gemini_timeout_s=timeout))


def test_blank_model_and_prompt() -> None:
    """빈 모델과 지시문을 명확한 설정 오류로 처리한다."""
    with pytest.raises(LLMPermanentError, match="CLOZE_GEMINI_MODEL"):
        gemini._http_options(Settings(gemini_model=" "))
    with pytest.raises(LLMPermanentError, match="prompt"):
        asyncio.run(GeminiClient(transport=FakeTransport()).ask_llm(" ", SCHEMA))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_json_numbers(constant: str) -> None:
    """스키마가 허용하더라도 표준 JSON이 아닌 숫자 상수는 거절한다."""
    transport = FakeTransport(deque(['{"value":' + constant + "}"]))
    with pytest.raises(LLMInvalidResponse):
        asyncio.run(GeminiClient(transport=transport).ask_llm("prompt", {"type": "object"}))


@pytest.mark.parametrize("action", ["cancel", "retry", "close"])
def test_during_backoff(action: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """백오프는 슬롯을 반환하고 취소·종료 후 추가 전송이나 연결 생성을 막는다."""

    async def scenario() -> None:
        waiting, resume = asyncio.Event(), asyncio.Event()

        async def delay(attempt: int) -> None:
            waiting.set()
            await resume.wait()

        monkeypatch.setattr(gemini, "_retry_delay", delay)
        transport = FakeTransport(deque([TimeoutError("private")]))
        factory = MagicMock(return_value=transport)
        monkeypatch.setattr(gemini, "_SDKTransport", factory)
        client = GeminiClient(Settings(gemini_max_concurrency=1))
        async with asyncio.timeout(2):
            task = asyncio.create_task(client.ask_llm("prompt", SCHEMA))
            try:
                await waiting.wait()
                if action == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                elif action == "close":
                    await client.aclose()
                    assert transport.closes == 1
                    resume.set()
                    with pytest.raises(LLMPermanentError, match="closed"):
                        await task
                else:
                    assert await client.ask_llm("other", SCHEMA) == {"ok": True}
                    assert not task.done()
                    assert transport.calls == 2
                    resume.set()
                    assert await task == {"ok": True}
                assert transport.calls == (3 if action == "retry" else 1)
                factory.assert_called_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
        assert transport.closes == 1

    asyncio.run(scenario())


def test_cancel_close_can_be_retried() -> None:
    """진행 중 요청을 기다리던 종료가 취소되어도 다시 닫을 수 있다."""

    async def scenario() -> None:
        """종료 취소 후 자원 누수나 잠금 교착 없이 마무리한다."""
        transport = FakeTransport(blocker=asyncio.Event())
        client = GeminiClient(transport=transport)
        task = asyncio.create_task(client.ask_llm("prompt", SCHEMA))
        await transport.entered.wait()
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await client.aclose()
        assert transport.closes == 1

    asyncio.run(scenario())


def test_sdk_receives_pil_image() -> None:
    """PIL 이미지를 SDK가 지원하는 contents 목록에 포함한다."""
    fake = MagicMock()
    fake.aio.models.generate_content = AsyncMock(return_value=MagicMock(text='{"ok":true}'))
    fake.aio.aclose = AsyncMock()
    adapter = object.__new__(gemini._SDKTransport)
    adapter._client = fake.aio
    adapter._model = Settings().gemini_model
    options = gemini._http_options(Settings(gemini_timeout_s=1.25))
    assert options.timeout == 1250
    assert options.retry_options is not None
    assert options.retry_options.attempts == 1
    image = Image.new("RGB", (2, 2))
    asyncio.run(adapter.generate("prompt", SCHEMA, image))
    assert fake.aio.models.generate_content.call_args.kwargs["contents"] == ["prompt", image]
