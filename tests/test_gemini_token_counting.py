"""실제 네트워크 없이 Gemini 토큰 계산과 공통 호출 정책을 검증한다."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google import genai
from google.genai import errors, types
from PIL import Image
from pydantic import SecretStr

from cloze.config import Settings
from cloze.core.errors import LLMInvalidResponse, LLMPermanentError, LLMTransientError
from cloze.infra import gemini


def _settings(**kwargs: Any) -> Settings:
    """환경 파일을 읽지 않는 합성 설정을 반환한다."""
    return Settings(
        **dict[str, Any](_env_file=None, gemini_api_key=SecretStr("fake-key")), **kwargs
    )


def _transport(total: object = 12) -> Any:
    """생성과 계산, 종료를 지원하는 비동기 전송 대역을 만든다."""
    return SimpleNamespace(
        count_tokens=AsyncMock(return_value=total),
        generate=AsyncMock(return_value='{"ok": true}'),
        aclose=AsyncMock(),
    )


@pytest.mark.parametrize("total", [0, 12])
def test_sdk_uses_async_count_and_configured_model(
    monkeypatch: pytest.MonkeyPatch, total: int
) -> None:
    """SDK 경계의 모델, 원문, 인증 및 SDK 재시도 비활성화를 검증한다."""
    sdk = SimpleNamespace(
        models=SimpleNamespace(
            count_tokens=AsyncMock(return_value=types.CountTokensResponse(total_tokens=total))
        ),
        aclose=AsyncMock(),
    )
    factory = Mock(return_value=SimpleNamespace(aio=sdk))
    monkeypatch.setattr(genai, "Client", factory)

    async def run() -> None:
        client = gemini.GeminiClient(_settings(gemini_model="test-model"))
        assert await client.count_tokens("한글 English 🙂") == total
        await client.aclose()

    asyncio.run(run())
    sdk.models.count_tokens.assert_awaited_once_with(model="test-model", contents="한글 English 🙂")
    assert factory.call_args.kwargs["api_key"] == "fake-key"
    assert factory.call_args.kwargs["http_options"].retry_options.attempts == 1
    sdk.aclose.assert_awaited_once()


@pytest.mark.parametrize("total", [None, -1, True, False, 1.5, "12"])
@pytest.mark.parametrize("sdk_boundary", [False, True])
def test_invalid_counts_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, total: object, sdk_boundary: bool
) -> None:
    """SDK 응답과 주입 전송 모두에서 잘못된 계산값을 거절한다."""
    transport = _transport(total)
    if sdk_boundary:
        models = SimpleNamespace(
            count_tokens=AsyncMock(return_value=SimpleNamespace(total_tokens=total))
        )
        monkeypatch.setattr(
            genai,
            "Client",
            Mock(return_value=SimpleNamespace(aio=SimpleNamespace(models=models))),
        )
        transport = gemini._SDKTransport(_settings())
    client = gemini.GeminiClient(_settings(), transport=transport)
    with pytest.raises(LLMInvalidResponse):
        asyncio.run(client.count_tokens("prompt"))
    if not sdk_boundary:
        transport.count_tokens.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "expected", "attempts"),
    [
        (errors.APIError(408, {}), LLMTransientError, 3),
        (errors.APIError(429, {}), LLMTransientError, 3),
        (errors.APIError(503, {}), LLMTransientError, 3),
        (httpx.ConnectError("sensitive"), LLMTransientError, 3),
        (TimeoutError("sensitive"), LLMTransientError, 3),
        (errors.APIError(400, {}), LLMPermanentError, 1),
        (errors.APIError(401, {}), LLMPermanentError, 1),
        (errors.APIError(403, {}), LLMPermanentError, 1),
        (errors.APIError(404, {}), LLMPermanentError, 1),
        (ValueError("sensitive"), LLMInvalidResponse, 1),
    ],
)
def test_error_classification_and_retry_limit(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception], attempts: int
) -> None:
    """오류 분류, 최대 시도 횟수 및 외부 오류 메시지 비노출을 검증한다."""
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_retry_delay", delay)
    transport = _transport()
    transport.count_tokens.side_effect = error
    client = gemini.GeminiClient(_settings(), transport=transport)
    with pytest.raises(expected) as caught:
        asyncio.run(client.count_tokens("prompt"))
    assert "sensitive" not in str(caught.value)
    assert transport.count_tokens.await_count == attempts
    assert delay.await_count == attempts - 1


def test_transient_failure_can_recover(monkeypatch: pytest.MonkeyPatch) -> None:
    """일시적 실패 뒤 세 번째 시도 성공과 백오프 인자를 검증한다."""
    delay = AsyncMock()
    monkeypatch.setattr(gemini, "_retry_delay", delay)
    transport = _transport()
    transport.count_tokens.side_effect = [TimeoutError(), TimeoutError(), 7]
    client = gemini.GeminiClient(_settings(), transport=transport)
    assert asyncio.run(client.count_tokens("prompt")) == 7
    assert [call.args for call in delay.await_args_list] == [(0,), (1,)]


@pytest.mark.parametrize("prompt", ["", " \n"])
def test_empty_input_does_not_send(prompt: str) -> None:
    """빈 지시문은 SDK 생성이나 전송 전에 거절한다."""
    transport = _transport()
    with pytest.raises(LLMPermanentError):
        asyncio.run(gemini.GeminiClient(_settings(), transport=transport).count_tokens(prompt))
    transport.count_tokens.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("bug"), asyncio.CancelledError()])
def test_unexpected_errors_propagate(error: BaseException) -> None:
    """프로그래밍 오류와 취소를 재시도하거나 숨기지 않는다."""
    transport = _transport()
    transport.count_tokens.side_effect = error
    with pytest.raises(type(error)):
        asyncio.run(gemini.GeminiClient(_settings(), transport=transport).count_tokens("prompt"))
    transport.count_tokens.assert_awaited_once()


def test_count_and_generation_share_slots_and_close_waits() -> None:
    """혼합 호출의 동시성 제한, 진행 중 종료 대기와 대기 요청 차단을 검증한다."""

    async def run() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def count(prompt: str) -> int:
            """슬롯 하나를 명시적으로 해제할 때까지 점유한다."""
            entered.set()
            await release.wait()
            return 2

        transport = _transport()
        transport.count_tokens.side_effect = count
        client = gemini.GeminiClient(_settings(gemini_max_concurrency=1), transport=transport)
        first = asyncio.create_task(client.count_tokens("first"))
        await entered.wait()
        queued = asyncio.create_task(client.ask_llm("second", {"type": "object"}))
        await asyncio.sleep(0)
        transport.generate.assert_not_awaited()
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        transport.aclose.assert_not_awaited()
        release.set()
        assert await first == 2
        with pytest.raises(LLMPermanentError, match="closed"):
            await queued
        await closing
        with pytest.raises(LLMPermanentError, match="closed"):
            await client.count_tokens("after close")
        transport.aclose.assert_awaited_once()

    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_backoff_releases_slot_and_close_blocks_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """재시도 대기 중 생성이 슬롯을 사용하고 종료 후 재시도가 차단되는지 검증한다."""

    async def run() -> None:
        waiting, release = asyncio.Event(), asyncio.Event()

        async def delay(attempt: int) -> None:
            """재시도 대기를 이벤트로 제어한다."""
            waiting.set()
            await release.wait()

        monkeypatch.setattr(gemini, "_retry_delay", delay)
        transport = _transport()
        transport.count_tokens.side_effect = TimeoutError()
        client = gemini.GeminiClient(_settings(gemini_max_concurrency=1), transport=transport)
        counting = asyncio.create_task(client.count_tokens("prompt"))
        await waiting.wait()
        assert await client.ask_llm("prompt", {"type": "object"}) == {"ok": True}
        await client.aclose()
        release.set()
        with pytest.raises(LLMPermanentError, match="closed"):
            await counting
        transport.count_tokens.assert_awaited_once()

    asyncio.run(asyncio.wait_for(run(), timeout=3))


@pytest.mark.parametrize("key", [None, SecretStr(" ")])
def test_count_requires_existing_secret_auth(
    monkeypatch: pytest.MonkeyPatch, key: SecretStr | None
) -> None:
    """기존 인증 검증이 토큰 계산에서도 SDK 생성 전에 적용되는지 확인한다."""
    factory = Mock()
    monkeypatch.setattr(genai, "Client", factory)
    settings = Settings(**dict[str, Any](_env_file=None, gemini_api_key=key))
    with pytest.raises(LLMPermanentError):
        asyncio.run(gemini.GeminiClient(settings).count_tokens("prompt"))
    factory.assert_not_called()


def test_sdk_generation_preserves_image_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """공통 호출 경로 변경 후에도 이미지와 JSON 스키마 전달을 유지한다."""
    generate = AsyncMock(return_value=SimpleNamespace(text='{"ok": true}'))
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=generate), aclose=AsyncMock())
    monkeypatch.setattr(genai, "Client", Mock(return_value=SimpleNamespace(aio=sdk)))
    image = Image.new("RGB", (1, 1))
    schema = {"type": "object", "required": ["ok"]}

    async def run() -> None:
        """가짜 SDK로 생성하고 종료한다."""
        client = gemini.GeminiClient(_settings(gemini_model="test-model"))
        assert await client.ask_llm("prompt", schema, image) == {"ok": True}
        await client.aclose()

    asyncio.run(run())
    assert generate.call_args.kwargs["model"] == "test-model"
    assert generate.call_args.kwargs["contents"] == ["prompt", image]
    config = generate.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema
