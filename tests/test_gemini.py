"""실제 API 키나 네트워크를 사용하지 않는 Gemini 경계 테스트."""

import asyncio
import json
import secrets
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google import genai
from google.genai import errors, types
from PIL import Image
from pydantic import SecretStr, ValidationError

from cloze.config import Settings
from cloze.core.errors import LLMInvalidResponse, LLMPermanentError, LLMTransientError
from cloze.infra import gemini
from cloze.infra.gemini import GeminiClient, JSONObject, LLMAttempt

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


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_concurrency_limit(limit: int) -> None:
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
                    return '{"ok":true}'
                finally:
                    active -= 1

            transport = MagicMock()
            transport.generate = AsyncMock(side_effect=send)
            transport.aclose = AsyncMock()
            client = GeminiClient(Settings(gemini_max_concurrency=limit), transport=transport)

            async def request(index: int) -> JSONObject:
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
                assert all(task.result() == {"ok": True} for task in tasks)
                assert peak == limit
                assert transport.generate.await_count == len(attempted)
            finally:
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
    assert_sdk_auth_and_options(sdk)
    assert_sdk_generation_options(fake)
    fake.aio.aclose.assert_awaited_once()


def assert_sdk_auth_and_options(sdk: MagicMock) -> None:
    """키 전달과 SDK 타임아웃·내부 재시도 설정을 검증한다."""
    assert sdk.call_args.kwargs["api_key"] == "test-placeholder"
    options = sdk.call_args.kwargs["http_options"]
    assert options.timeout == 60000
    assert options.retry_options.attempts == 1


def assert_sdk_generation_options(fake: MagicMock) -> None:
    """생성 모델·이미지 입력·JSON 응답 설정을 검증한다."""
    kwargs = fake.aio.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["contents"] == ["prompt"]
    assert kwargs["config"].response_json_schema == SCHEMA
    assert kwargs["config"].response_mime_type == "application/json"


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
                await check_backoff_action(action, client, transport, task, resume)
                assert transport.calls == (3 if action == "retry" else 1)
                factory.assert_called_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
        assert transport.closes == 1

    asyncio.run(scenario())


async def check_backoff_action(
    action: str,
    client: GeminiClient,
    transport: FakeTransport,
    task: asyncio.Task[JSONObject],
    resume: asyncio.Event,
) -> None:
    """동일한 대기 시점에서 취소·종료·재시도 각각의 결과를 검증한다."""

    async def cancel() -> None:
        """취소를 추가 전송 없이 전파한다."""
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def close() -> None:
        """종료 후 백오프 재개가 연결을 다시 만들지 않는다."""
        await client.aclose()
        assert transport.closes == 1
        resume.set()
        with pytest.raises(LLMPermanentError, match="closed"):
            await task

    async def retry() -> None:
        """백오프 중 다른 요청이 슬롯을 쓰고 원래 요청도 재개한다."""
        assert await client.ask_llm("other", SCHEMA) == {"ok": True}
        assert not task.done()
        assert transport.calls == 2
        resume.set()
        assert await task == {"ok": True}

    await {"cancel": cancel, "close": close, "retry": retry}[action]()


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


PRIVATE_KEY = "fake-private-api-key-review"
PRIVATE_BODY = "fake-private-response-body-review"


def chain_log(error: BaseException | None) -> str:
    """억제 플래그를 무시하고 예외 체인을 직접 읽는 로거를 재현한다."""
    messages: list[str] = []
    while error is not None:
        messages.append(str(error))
        error = error.__cause__ or error.__context__
    return "\n".join(messages)


def assert_private_chain_is_detached(error: Exception) -> None:
    """키와 본문이 공개 traceback과 직접 체인 로깅 모두에서 보이지 않는다."""
    logged = chain_log(error) + "".join(traceback.format_exception(error))
    assert PRIVATE_KEY not in logged
    assert PRIVATE_BODY not in logged
    assert (error.__cause__, error.__context__) == (None, None)


@pytest.mark.parametrize("private", [PRIVATE_KEY, PRIVATE_BODY], ids=["api-key", "body"])
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("auth", LLMPermanentError),
        ("value", LLMInvalidResponse),
        ("timeout", LLMTransientError),
        ("json", LLMInvalidResponse),
    ],
)
def test_generation_detaches_private_chain(
    private: str,
    kind: str,
    expected: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """분류·재시도는 유지하면서 원본 예외 context를 공개 경계에서 제거한다."""
    failures: dict[str, str | Exception] = {
        "auth": errors.APIError(401, {"message": private}),
        "value": ValueError(private),
        "timeout": TimeoutError(private),
        "json": private,
    }
    transport = FakeTransport(deque([failures[kind]] * 3))
    monkeypatch.setattr(gemini, "_retry_delay", AsyncMock())
    records: list[LLMAttempt] = []
    with pytest.raises(expected) as caught:
        asyncio.run(
            GeminiClient(transport=transport).ask_llm("prompt", SCHEMA, on_attempt=records.append)
        )
    assert_private_chain_is_detached(caught.value)
    assert transport.calls == (3 if kind == "timeout" else 1)
    assert len(records) == transport.calls


@pytest.mark.parametrize("stage", ["initialize", "configure", "decode"])
@pytest.mark.parametrize(
    "failure",
    [
        ValueError(PRIVATE_BODY),
        json.JSONDecodeError(PRIVATE_BODY, PRIVATE_BODY, 0),
        ValidationError.from_exception_data(
            "SDK", [{"type": "int_parsing", "loc": ("value",), "input": PRIVATE_BODY}]
        ),
    ],
)
def test_sdk_errors_detach_private_context(
    stage: str,
    failure: ValueError,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """초기화·요청 구성·SDK 응답 실패의 예외 체인을 직접 검사한다."""
    sdk = MagicMock()
    sdk.aio.models.generate_content = AsyncMock(side_effect=failure)
    sdk.aio.aclose = AsyncMock()
    factory = MagicMock(return_value=sdk)
    if stage == "initialize":
        factory.side_effect = failure
    if stage == "configure":
        monkeypatch.setattr(types, "GenerateContentConfig", MagicMock(side_effect=failure))
    monkeypatch.setattr(genai, "Client", factory)
    client = GeminiClient(Settings(gemini_api_key=SecretStr(PRIVATE_KEY)))
    expected = LLMInvalidResponse if stage == "decode" else LLMPermanentError
    with pytest.raises(expected) as caught:
        asyncio.run(client.ask_llm("prompt", SCHEMA))
    assert_private_chain_is_detached(caught.value)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError(PRIVATE_BODY), LLMTransientError),
        (httpx.ConnectError(PRIVATE_KEY), LLMTransientError),
        (errors.APIError(503, {"message": PRIVATE_BODY}), LLMTransientError),
        (errors.APIError(401, {"message": PRIVATE_KEY}), LLMPermanentError),
        (ValueError(PRIVATE_BODY), LLMPermanentError),
    ],
)
def test_close_errors_are_private_and_retryable(
    failure: Exception,
    expected: type[Exception],
) -> None:
    """종료 오류는 생성 기록을 추가하지 않고 명시적 재종료를 허용한다."""

    async def scenario() -> None:
        """실패한 종료 뒤 잠금·슬롯·연결을 재사용해 정리한다."""
        transport = MagicMock()
        transport.generate = AsyncMock(return_value='{"ok":true}')
        transport.aclose = AsyncMock(side_effect=[failure, None])
        client = GeminiClient(transport=transport)
        records: list[LLMAttempt] = []
        await client.ask_llm("prompt", SCHEMA, on_attempt=records.append)
        with pytest.raises(expected) as caught:
            await client.aclose()
        assert_private_chain_is_detached(caught.value)
        await client.aclose()
        await client.aclose()
        assert (transport.aclose.await_count, len(records)) == (2, 1)
        with pytest.raises(LLMPermanentError, match="closed"):
            await client.ask_llm("prompt", SCHEMA)

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_close_inside_transport_fails_without_deadlock() -> None:
    """자기 슬롯을 기다리는 종료를 즉시 거절하고 요청은 계속 실행할 수 있다."""

    async def scenario() -> None:
        """직접 await로 재진입한 종료가 클라이언트를 닫지 않는지 확인한다."""
        transport = MagicMock()
        client = GeminiClient(Settings(gemini_max_concurrency=1), transport=transport)

        async def send(*args: object) -> str:
            """전송 중 종료를 시도한 뒤 정상 응답한다."""
            try:
                raise ValueError(PRIVATE_KEY)
            except ValueError:
                with pytest.raises(LLMPermanentError, match="active request") as caught:
                    await client.aclose()
                assert_private_chain_is_detached(caught.value)
            return '{"ok":true}'

        transport.generate = AsyncMock(side_effect=send)
        transport.aclose = AsyncMock()
        assert await client.ask_llm("prompt", SCHEMA) == {"ok": True}
        await client.aclose()
        transport.aclose.assert_awaited_once()

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_unhandled_reentrant_close_records_one_permanent_attempt() -> None:
    """전송이 종료 오용 오류를 전달하면 영구 실패 한 번만 기록한다."""

    async def scenario() -> None:
        """오류 뒤에도 외부 종료로 연결을 정리할 수 있다."""
        transport = MagicMock(aclose=AsyncMock())
        client = GeminiClient(transport=transport)
        transport.generate = AsyncMock(side_effect=lambda *args: None)

        async def send(*args: object) -> str:
            """전송 내부 종료 오용 오류를 그대로 전파한다."""
            await client.aclose()
            return "{}"

        transport.generate.side_effect = send
        records: list[LLMAttempt] = []
        with pytest.raises(LLMPermanentError):
            await client.ask_llm("prompt", SCHEMA, on_attempt=records.append)
        assert [record.outcome for record in records] == ["permanent"]
        await client.aclose()

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


@pytest.mark.parametrize("nested", [False, True])
def test_reentrant_close_during_nested_request_or_cleanup(nested: bool) -> None:
    """중첩 요청이 끝나도 바깥 전송과 종료 자체의 재진입을 계속 거절한다."""

    async def scenario() -> None:
        """같은 Task의 중첩을 한정된 시간 안에서 재현한다."""
        transport = MagicMock()
        client = GeminiClient(Settings(gemini_max_concurrency=2), transport=transport)

        async def closing() -> None:
            """연결 정리 중 클라이언트 종료로 재진입한다."""
            with pytest.raises(LLMPermanentError, match="active request"):
                await client.aclose()

        async def send(prompt: str, schema: JSONObject, image: Image.Image | None) -> str:
            """내부 요청 뒤에도 바깥 요청의 종료 금지를 확인한다."""
            if prompt == "outer":
                await client.ask_llm("inner", SCHEMA)
                await closing()
            return '{"ok":true}'

        transport.generate = AsyncMock(side_effect=send)
        transport.aclose = AsyncMock(side_effect=closing)
        if nested:
            await client.ask_llm("outer", SCHEMA)
        await client.aclose()
        transport.aclose.assert_awaited_once()

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


@pytest.mark.parametrize("closing", [False, True])
def test_public_errors_detach_ambient_context(closing: bool) -> None:
    """다른 예외 처리기 안에서 호출해도 Python이 붙인 context를 제거한다."""

    async def scenario() -> None:
        """활성 예외가 있는 상황의 생성·종료 실패를 직접 로깅한다."""
        transport = MagicMock()
        transport.generate = AsyncMock(side_effect=ValueError(PRIVATE_BODY))
        transport.aclose = AsyncMock(side_effect=ValueError(PRIVATE_BODY))
        client = GeminiClient(transport=transport)
        try:
            raise ValueError(PRIVATE_KEY)
        except ValueError:
            operation = client.aclose() if closing else client.ask_llm("prompt", SCHEMA)
            with pytest.raises((LLMPermanentError, LLMInvalidResponse)) as caught:
                await operation
            assert_private_chain_is_detached(caught.value)

    asyncio.run(scenario())
