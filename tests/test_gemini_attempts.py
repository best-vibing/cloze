"""실제 네트워크 없이 생성 시도 기록과 기존 수명·재시도 계약을 검증한다."""

import asyncio
import socket
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from google import genai
from google.genai import errors, types

from cloze.config import Settings
from cloze.core.errors import LLMInvalidResponse, LLMPermanentError, LLMTransientError
from cloze.infra import gemini
from cloze.infra.gemini import GeminiClient, GeminiResponse, JSONObject, LLMAttempt

SCHEMA: JSONObject = {"type": "object", "required": ["ok"]}
PRIVATE = "fake-api-key-and-response-body"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """개발자 설정과 실제 SDK·네트워크를 차단하고 백오프를 생략한다."""
    monkeypatch.chdir(tmp_path)
    for suffix in ("API_KEY", "MODEL", "TIMEOUT_S", "MAX_CONCURRENCY"):
        monkeypatch.delenv(f"CLOZE_GEMINI_{suffix}", raising=False)
    blocked = MagicMock(side_effect=AssertionError("network forbidden"))
    monkeypatch.setattr(genai, "Client", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(gemini, "_retry_delay", AsyncMock())


def fake_transport(responses: list[GeminiResponse | str | Exception]) -> MagicMock:
    """준비한 응답과 오류를 순서대로 반환하는 Fake 전송을 만든다."""
    transport = MagicMock()
    transport.generate = AsyncMock(side_effect=responses)
    transport.aclose = AsyncMock()
    return transport


@pytest.mark.parametrize(
    ("responses", "outcomes", "error"),
    [
        ([GeminiResponse('{"ok":true}', 12, 3)], ["ok"], None),
        ([TimeoutError(), TimeoutError(), '{"ok":true}'], ["transient", "transient", "ok"], None),
        ([TimeoutError()] * 3, ["transient"] * 3, LLMTransientError),
        ([errors.APIError(401, {"message": PRIVATE})], ["permanent"], LLMPermanentError),
        ([PRIVATE], ["invalid"], LLMInvalidResponse),
        ([GeminiResponse("{}", 7, 2)], ["invalid"], LLMInvalidResponse),
        ([ValueError(PRIVATE)], ["invalid"], LLMInvalidResponse),
    ],
)
def test_attempt_outcomes(
    responses: list[GeminiResponse | str | Exception],
    outcomes: list[str],
    error: type[Exception] | None,
) -> None:
    """각 시도에 한 번만 기록하며 공개 예외·기록에 본문을 넣지 않는다."""
    transport = fake_transport(responses)
    records: list[LLMAttempt] = []
    client = GeminiClient(Settings(gemini_model="test-model"), transport=transport)
    request = client.ask_llm("prompt", SCHEMA, on_attempt=records.append)
    if error is None:
        assert isinstance(asyncio.run(request), dict)
    else:
        with pytest.raises(error) as caught:
            asyncio.run(request)
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    assert_record_order(records, outcomes)
    assert transport.generate.await_count == len(records)
    assert_record_metadata(records, responses[-1])


def assert_record_order(records: list[LLMAttempt], outcomes: list[str]) -> None:
    """시도별 결과와 1부터 시작하는 번호를 확인한다."""
    assert [r.outcome for r in records] == outcomes
    assert [r.attempt for r in records] == list(range(1, len(outcomes) + 1))


def assert_record_metadata(records: list[LLMAttempt], response: object) -> None:
    """기록은 민감정보 없이 시간·모델·실제 사용량만 보존한다."""
    assert all(r.latency_ms >= 0 and r.model == "test-model" for r in records)
    assert PRIVATE not in repr(records)
    assert (records[-1].input_tokens, records[-1].output_tokens) == (
        getattr(response, "input_tokens", None),
        getattr(response, "output_tokens", None),
    )


@pytest.mark.parametrize("usage", [None, (14, 5), (0, 0)])
def test_sdk_usage(usage: tuple[int, int] | None) -> None:
    """실제 SDK 응답 타입의 usage_metadata에서 추정치 없이 사용량을 읽는다."""
    metadata = (
        None
        if usage is None
        else types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage[0], candidates_token_count=usage[1], thoughts_token_count=99
        )
    )
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text='{"ok":true}')]))],
        usage_metadata=metadata,
    )
    adapter = object.__new__(gemini._SDKTransport)
    adapter._client = MagicMock()
    adapter._client.models.generate_content = AsyncMock(return_value=response)
    adapter._model = "test-model"
    records: list[LLMAttempt] = []
    result = asyncio.run(
        GeminiClient(transport=adapter).ask_llm("prompt", SCHEMA, on_attempt=records.append)
    )
    assert result == {"ok": True}
    assert (records[0].input_tokens, records[0].output_tokens) == (usage or (None, None))


@pytest.mark.parametrize("value", [None, True, -1, "3", 2.5, MagicMock()])
def test_invalid_usage(value: object) -> None:
    """누락·비정상 SDK 토큰 값은 실제 사용량으로 기록하지 않는다."""
    assert gemini._usage_tokens(value) is None


def test_sdk_text_failure_preserves_usage() -> None:
    """SDK 텍스트 추출이 실패해도 이미 수신한 사용량은 버리지 않는다."""
    response = MagicMock(
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=4, candidates_token_count=0
        )
    )
    type(response).text = PropertyMock(side_effect=ValueError(PRIVATE))
    assert gemini._sdk_response(response) == GeminiResponse(None, 4, 0)


@pytest.mark.parametrize("outcome", [GeminiResponse("{}"), TimeoutError(), ValueError(PRIVATE)])
@pytest.mark.parametrize("callback_error", [LLMTransientError, LLMPermanentError, ValueError])
def test_callback_failure_does_not_retry(
    outcome: GeminiResponse | Exception,
    callback_error: type[Exception],
) -> None:
    """성공·실패를 알리는 콜백 자체의 예외는 재시도 없이 전파한다."""
    transport = fake_transport([outcome])
    callback = MagicMock(side_effect=callback_error("callback"))
    with pytest.raises(callback_error, match="callback"):
        asyncio.run(GeminiClient(transport=transport).ask_llm("prompt", {}, on_attempt=callback))
    callback.assert_called_once()
    transport.generate.assert_awaited_once()
    gemini._retry_delay.assert_not_awaited()  # type: ignore[attr-defined]


def test_none_and_legacy_transport() -> None:
    """콜백과 사용량이 없는 기존 문자열 전송도 검증된 dict를 반환한다."""
    transport = fake_transport(['{"ok":true}'])
    assert asyncio.run(
        GeminiClient(transport=transport).ask_llm("prompt", SCHEMA, on_attempt=None)
    ) == {"ok": True}


def test_concurrency_cancel_and_close() -> None:
    """취소·슬롯 제한·종료 대기와 요청별 기록 격리를 함께 검증한다."""

    async def scenario() -> None:
        """이벤트로 활성 전송을 고정하고 취소 후 자원을 회수한다."""
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(prompt: str, schema: JSONObject, image: object) -> GeminiResponse:
            """한 요청을 고정하고 나머지는 즉시 반환한다."""
            entered.set()
            await release.wait()
            return GeminiResponse('{"ok":true}', len(prompt), 0)

        transport = fake_transport([])
        transport.generate.side_effect = send
        client = GeminiClient(Settings(gemini_max_concurrency=1), transport=transport)
        first: list[LLMAttempt] = []
        second: list[LLMAttempt] = []
        async with asyncio.timeout(3):
            task = asyncio.create_task(client.ask_llm("one", SCHEMA, on_attempt=first.append))
            await entered.wait()
            other = asyncio.create_task(client.ask_llm("second", SCHEMA, on_attempt=second.append))
            await asyncio.sleep(0)
            assert transport.generate.await_count == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            closing = asyncio.create_task(client.aclose())
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            assert await other == {"ok": True}
            await closing
            await client.aclose()
        transport.aclose.assert_awaited_once()
        with pytest.raises(LLMPermanentError):
            await client.ask_llm("closed", {}, on_attempt=first.append)
        assert (first, len(second), second[0].input_tokens) == ([], 1, 6)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_backoff_releases_slot(cancel: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """백오프 중 다른 요청이 실행되며 대기·취소를 전송 시간에 넣지 않는다."""

    async def scenario() -> None:
        """백오프를 이벤트로 제어하고 단조 시계로 전송 시간만 확인한다."""
        waiting, resume = asyncio.Event(), asyncio.Event()
        clock = iter([0.0, 0.01, 100.0, 100.01, 200.0, 200.01])
        monkeypatch.setattr(gemini, "monotonic", lambda: next(clock))

        async def delay(attempt: int) -> None:
            """첫 실패의 백오프를 명시적으로 중단한다."""
            waiting.set()
            await resume.wait()

        monkeypatch.setattr(gemini, "_retry_delay", delay)
        transport = fake_transport([TimeoutError(), "{}", "{}"])
        client = GeminiClient(Settings(gemini_max_concurrency=1), transport=transport)
        records: list[LLMAttempt] = []
        others: list[LLMAttempt] = []
        async with asyncio.timeout(3):
            task = asyncio.create_task(client.ask_llm("first", {}, on_attempt=records.append))
            await waiting.wait()
            assert await client.ask_llm("other", {}, on_attempt=others.append) == {}
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                resume.set()
                assert await task == {}
        assert_record_order(records, ["transient"] if cancel else ["transient", "ok"])
        assert_backoff_latency(records, others)
        await client.aclose()

    asyncio.run(scenario())


def assert_backoff_latency(records: list[LLMAttempt], others: list[LLMAttempt]) -> None:
    """동시 요청 번호와 백오프를 제외한 전송 시간을 확인한다."""
    assert [r.attempt for r in others] == [1]
    assert [r.latency_ms for r in records + others] == pytest.approx([10] * len(records + others))
