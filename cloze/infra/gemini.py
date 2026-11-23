"""설정 기반 Gemini 비동기 호출과 응답 검증."""

import asyncio
import json
import math
import re
import secrets
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal, NoReturn, Protocol, cast

import httpx
from google import genai
from google.genai import errors, types
from jsonschema import exceptions, validators
from PIL.Image import Image
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from cloze.config import Settings
from cloze.core.errors import (
    LLMInvalidResponse,
    LLMPermanentError,
    LLMQuotaExhausted,
    LLMTransientError,
)

type JSONObject = dict[str, Any]
type AttemptOutcome = Literal["ok", "invalid", "transient", "permanent", "quota"]

# 408·5xx·전송 오류의 최초 포함 시도 횟수. 429는 Settings의 gemini_rate_limit_* 값을 따른다.
_TRANSIENT_ATTEMPTS = 3
_RETRY_DELAY = re.compile(r"(\d{1,6}(?:\.\d{1,9})?)s")


@dataclass(frozen=True)
class LLMAttempt:
    """민감한 요청·응답 내용을 포함하지 않는 한 번의 생성 시도 기록."""

    attempt: int
    outcome: AttemptOutcome
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    model: str


@dataclass(frozen=True)
class GeminiResponse:
    """전송 응답과 실제 사용량. text=None은 SDK 텍스트 추출 실패다."""

    text: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _usage_tokens(value: object) -> int | None:
    """SDK 사용량의 비음수 정수만 보존하며 누락·비정상 값은 알 수 없음으로 둔다."""
    return value if type(value) is int and value >= 0 else None


class GeminiTransport(Protocol):
    """실제 SDK와 오프라인 테스트가 공유하는 전송 경계."""

    async def generate(
        self, prompt: str, schema: JSONObject, image: Image | None
    ) -> GeminiResponse | str:
        """텍스트와 사용량을 반환한다. 기존 문자열 전송도 지원한다."""
        ...

    async def aclose(self) -> None:
        """전송 자원을 해제한다."""
        ...


class GeminiTokenTransport(GeminiTransport, Protocol):
    """기존 생성 전송과 호환되는 선택적 토큰 계산 경계."""

    async def count_tokens(self, prompt: str) -> int:
        """현재 모델의 입력 토큰 수를 계산한다."""
        ...


def _http_options(settings: Settings) -> types.HttpOptions:
    """초 단위 설정을 SDK 밀리초로 변환하고 내부 재시도를 끈다."""
    timeout = settings.gemini_timeout_s
    if not math.isfinite(timeout) or timeout <= 0:
        raise LLMPermanentError("Gemini timeout must be a positive finite value")
    if not settings.gemini_model.strip():
        raise LLMPermanentError("CLOZE_GEMINI_MODEL is required")
    return types.HttpOptions(
        timeout=max(1, math.ceil(timeout * 1000)),
        retry_options=types.HttpRetryOptions(attempts=1),
    )


class _SDKTransport:
    """google-genai 2.33의 비동기 클라이언트 어댑터."""

    def __init__(self, settings: Settings) -> None:
        """키와 설정을 확인하고 네트워크 호출 없이 SDK를 준비한다."""
        if settings.gemini_api_key is None:
            raise LLMPermanentError("CLOZE_GEMINI_API_KEY is required")
        key = settings.gemini_api_key.get_secret_value().strip()
        if not key:
            raise LLMPermanentError("CLOZE_GEMINI_API_KEY must not be empty")
        self._client = genai.Client(api_key=key, http_options=_http_options(settings)).aio
        self._model = settings.gemini_model

    async def generate(
        self, prompt: str, schema: JSONObject, image: Image | None
    ) -> GeminiResponse:
        """선택적 PIL 이미지와 JSON Schema를 SDK에 전달한다.

        Args:
            prompt: 호출자가 구성한 지시문.
            schema: 응답 객체의 JSON Schema.
            image: 메모리에 있는 PIL 이미지 또는 None.

        Returns:
            응답 텍스트와 SDK가 보고한 실제 사용량.
        """
        contents: list[types.PartUnion] = [prompt]
        if image is not None:
            contents.append(image)
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json", response_json_schema=schema
            )
        except ValueError:
            raise LLMPermanentError("Invalid Gemini request configuration") from None
        response = await self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        return _sdk_response(response)

    async def count_tokens(self, prompt: str) -> int:
        """생성과 같은 모델로 SDK의 비동기 토큰 계산을 요청한다."""
        response = await self._client.models.count_tokens(model=self._model, contents=prompt)
        return _validated_token_count(getattr(response, "total_tokens", None))

    async def aclose(self) -> None:
        """SDK의 비동기 HTTP 연결을 해제한다."""
        await self._client.aclose()


def _sdk_response(response: types.GenerateContentResponse) -> GeminiResponse:
    """텍스트 추출 실패에서도 응답에 포함된 실제 사용량을 보존한다."""
    usage = getattr(response, "usage_metadata", None)
    try:
        raw = response.text or ""
    except ValueError:
        raw = None
    return GeminiResponse(
        raw if isinstance(raw, str) else None,
        _usage_tokens(getattr(usage, "prompt_token_count", None)),
        _usage_tokens(getattr(usage, "candidates_token_count", None)),
    )


class _RateLimited(LLMTransientError):
    """분당 한도이거나 어느 한도인지 모르는 429. 재시도 판단에만 쓰고 밖으로 내보내지 않는다.

    대기 시간과 한도 종류를 예외 속성으로 들고 다닌다. 재시도 루프는 이미 분류된 예외만
    받으므로, 원본 APIError를 다시 읽지 않고 같은 자리에서 판단할 수 있다.
    """

    def __init__(self, retry_after_s: float | None, per_minute: bool) -> None:
        super().__init__("Gemini request rate limit exceeded")
        self.retry_after_s = retry_after_s
        self.per_minute = per_minute


def _error_details(error: errors.APIError) -> list[dict[str, Any]]:
    """SDK가 보관한 응답 JSON에서 google.rpc 상세 목록만 꺼낸다. 모양이 다르면 빈 목록."""
    body = error.details.get("error") if isinstance(error.details, dict) else None
    items = body.get("details") if isinstance(body, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _detail_type(item: dict[str, Any]) -> str:
    kind = item.get("@type")
    return kind.rsplit("/", 1)[-1] if isinstance(kind, str) else ""


def _retry_after(details: list[dict[str, Any]]) -> float | None:
    """google.rpc.RetryInfo의 retryDelay("59s" 형식)를 초로 바꾼다. 없거나 이상하면 None."""
    for item in details:
        delay = item.get("retryDelay")
        if _detail_type(item) == "google.rpc.RetryInfo" and isinstance(delay, str):
            match = _RETRY_DELAY.fullmatch(delay)
            if match:
                return float(match.group(1))
    return None


def _quota_ids(details: list[dict[str, Any]]) -> list[str]:
    """google.rpc.QuotaFailure의 quotaId 목록. 예: GenerateRequestsPerMinutePerProjectPerModel."""
    ids: list[str] = []
    for item in details:
        violations = item.get("violations")
        if _detail_type(item) == "google.rpc.QuotaFailure" and isinstance(violations, list):
            ids += [v["quotaId"] for v in violations if isinstance(v, dict) and "quotaId" in v]
    return [quota for quota in ids if isinstance(quota, str)]


def _classify_rate_limit(error: errors.APIError) -> Exception:
    """429를 하루 한도, 분당 한도, 구분할 수 없음으로 나눈다. 본문은 메시지에 넣지 않는다."""
    details = _error_details(error)
    quota_ids = _quota_ids(details)
    if any("PerDay" in quota for quota in quota_ids):
        return LLMQuotaExhausted("Gemini daily request quota exhausted")
    per_minute = any("PerMinute" in quota for quota in quota_ids)
    return _RateLimited(_retry_after(details), per_minute)


def _classify_api_error(error: errors.APIError) -> Exception:
    """외부 오류 본문을 노출하지 않고 상태 코드와 429의 한도 종류만 분류한다."""
    if error.code == 429:
        return _classify_rate_limit(error)
    if error.code == 408 or 500 <= error.code < 600:
        return LLMTransientError("Gemini service temporarily unavailable")
    return LLMPermanentError("Gemini authentication, model or request configuration failed")


def _attempt_outcome(error: Exception) -> AttemptOutcome:
    """이미 분류된 생성 오류를 기록용 결과로 변환한다."""
    if isinstance(error, LLMQuotaExhausted):
        return "quota"
    if isinstance(error, LLMInvalidResponse):
        return "invalid"
    if isinstance(error, LLMTransientError):
        return "transient"
    return "permanent"


def _notify_attempt(callback: Callable[[LLMAttempt], None] | None, record: LLMAttempt) -> None:
    """Gemini 오류 처리 밖에서 콜백을 호출하여 콜백 실패의 재전송을 막는다."""
    if callback is not None:
        callback(record)


def _raise_detached(error: Exception) -> NoReturn:
    """처리기 밖에서 원본 예외 체인·traceback을 분리해 공개 오류만 올린다."""
    try:
        raise error.with_traceback(None) from None
    except (LLMInvalidResponse, LLMTransientError, LLMPermanentError):
        # 호출자가 다른 예외를 처리 중이면 raise가 context를 다시 붙이므로 직후 제거한다.
        error.__context__ = None
        error.__cause__ = None
        raise


async def _close_once(transport: GeminiTransport) -> None:
    """한 번의 종료 실패를 안전한 공통 오류로 변환하며 자동 재시도하지 않는다."""
    try:
        await transport.aclose()
    except errors.APIError as error:
        failure = _classify_api_error(error)
    except (httpx.TransportError, TimeoutError):
        failure = LLMTransientError("Gemini transport close temporarily failed")
    except ValueError:
        failure = LLMPermanentError("Invalid Gemini transport close configuration")
    else:
        return
    _raise_detached(failure)


def _validated_token_count(total: object) -> int:
    """누락값, bool, 음수 및 정수가 아닌 토큰 계산값을 거절한다."""
    if type(total) is not int or total < 0:
        raise LLMInvalidResponse("Gemini token count must be a non-negative integer")
    return total


def _validator(schema: JSONObject) -> validators.Draft202012Validator:
    """로컬 스키마를 확인하고 외부 참조를 다운로드하지 않는 검증기를 만든다."""
    try:
        validators.Draft202012Validator.check_schema(schema)
    except exceptions.SchemaError:
        raise LLMPermanentError("Invalid response JSON Schema") from None
    resource = Resource.from_contents(schema, default_specification=DRAFT202012)
    registry: Registry[Any] = Registry().with_resource("", resource)
    _check_references(resource, registry.resolver())
    return validators.Draft202012Validator(schema, registry=registry)


def _check_references(resource: Resource[Any], resolver: Any) -> None:
    """스키마 위치의 참조를 전송 전에 검사하며 외부 자원을 가져오지 않는다."""
    resolver = resolver.in_subresource(resource)
    _check_resource_references(resource, resolver)
    for child in resource.subresources():
        _check_references(child, resolver)


def _check_resource_references(resource: Resource[Any], resolver: Any) -> None:
    """스키마 리소스의 정적 참조와 동적 참조를 사전에 검증한다."""
    for keyword in ("$ref", "$dynamicRef"):
        reference = resource.contents.get(keyword) if isinstance(resource.contents, dict) else None
        if reference is not None:
            try:
                resolver.lookup(reference)
            except Unresolvable:
                raise LLMPermanentError("Unresolvable response schema reference") from None


def _reject_constant(value: str) -> None:
    """표준 JSON에 없는 NaN과 Infinity 상수를 거절한다."""
    raise ValueError("Non-finite JSON number")


def _parse_response(raw: str, validator: validators.Draft202012Validator) -> JSONObject:
    """JSON 객체를 파싱하고 로컬 스키마로 검증한다."""
    try:
        result: object = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise LLMInvalidResponse("Gemini response is not valid JSON") from None
    if not isinstance(result, dict):
        raise LLMInvalidResponse("Gemini response must be a JSON object")
    try:
        validator.validate(result)
    except (exceptions.ValidationError, Unresolvable):
        raise LLMInvalidResponse("Gemini response does not match its JSON Schema") from None
    return cast(JSONObject, result)


async def _retry_delay(attempt: int) -> None:
    """지수 백오프에 최대 1초의 지연을 더한다."""
    await asyncio.sleep(2**attempt + secrets.randbelow(1000) / 1000)


async def _rate_limit_delay(seconds: float) -> None:
    """429 대기. 테스트가 실제로 기다리지 않도록 바꿔 끼울 수 있게 따로 둔다."""
    await asyncio.sleep(seconds)


def _jitter() -> float:
    """여러 호출이 같은 순간에 다시 몰리지 않도록 0~1초를 더한다."""
    return secrets.randbelow(1000) / 1000


class _RetryBudget:
    """한 번의 ask_llm·count_tokens 호출 안에서 재시도 횟수와 429 대기 합계를 센다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._transient = 0
        self._rate_limited = 0
        self._waited_s = 0.0

    async def wait_or_raise(self, failure: LLMTransientError) -> None:
        """다시 시도할 수 있으면 기다리고, 아니면 공개 오류를 올린다. 대기는 세마포어 밖이다."""
        if isinstance(failure, _RateLimited):
            await self._wait_rate_limit(failure)
            return
        self._transient += 1
        if self._transient >= _TRANSIENT_ATTEMPTS:
            raise LLMTransientError(
                f"Gemini request failed after {self._transient} attempts"
            ) from None
        await _retry_delay(self._transient - 1)

    async def _wait_rate_limit(self, failure: _RateLimited) -> None:
        settings = self._settings
        if failure.retry_after_s is not None:
            delay = failure.retry_after_s + _jitter()
        else:
            delay = settings.gemini_rate_limit_base_s * 2**self._rate_limited + _jitter()
        self._rate_limited += 1
        # 남은 대기 한도보다 긴 대기는 하지 않는다. 기다린 뒤에도 실패가 정해져 있으므로
        # 작업 제한 시간만 쓴다.
        if (
            self._rate_limited >= settings.gemini_rate_limit_attempts
            or self._waited_s + delay > settings.gemini_rate_limit_max_wait_s
        ):
            raise _rate_limit_exhausted(failure, self._rate_limited) from None
        self._waited_s += delay
        await _rate_limit_delay(delay)


def _rate_limit_exhausted(failure: _RateLimited, attempts: int) -> Exception:
    """기다릴 수 있는 만큼 기다린 뒤의 429. 분당 한도라고 밝힌 것만 일시적 오류로 남긴다."""
    if failure.per_minute:
        return LLMTransientError(f"Gemini rate limit persisted after {attempts} attempts")
    return LLMQuotaExhausted("Gemini request quota appears exhausted")


def _check_input(prompt: str, image: Image | None) -> None:
    """지시문과 이미지의 실행 시점 타입 및 빈 지시문을 검사한다."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise LLMPermanentError("Gemini prompt must not be empty")
    if image is not None and not isinstance(image, Image):
        raise LLMPermanentError("Gemini image must be a PIL.Image.Image")


class GeminiClient:
    """하나의 이벤트 루프에서 재사용할 호출 제한과 전송 수명을 관리한다.

    여러 호출은 같은 인스턴스를 공유해야 설정된 동시 상한이 적용된다.
    종료는 진행 중 호출을 기다리며, 종료가 취소되면 aclose를 다시 호출할 수 있다.
    전송 중 같은 Task의 aclose 호출은 영구 오류로 거절한다. 전송이 별도 종료 Task를
    만들고 기다리는 간접 순환은 탐지하지 못하므로 종료는 애플리케이션 수명 코드에서 한다.
    종료는 활성 전송만 기다린다. 백오프·슬롯 대기 요청은 다음 진입에서 closed로 실패한다.
    """

    def __init__(
        self, settings: Settings | None = None, *, transport: GeminiTransport | None = None
    ) -> None:
        """설정과 선택적 테스트 전송을 보관한다. 키는 아직 검사하지 않는다."""
        self._settings = settings if settings is not None else Settings()
        self._transport = transport
        self._max_concurrency = self._settings.gemini_max_concurrency
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._active_tasks: set[asyncio.Task[Any]] = set()

    @contextmanager
    def _active_request(self) -> Iterator[None]:
        """요청·종료 Task를 추적하고 중첩 실행·실패·취소에서도 복구한다."""
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Gemini operations require an asyncio task")
        already_active = task in self._active_tasks
        self._active_tasks.add(task)
        try:
            yield
        finally:
            if not already_active:
                self._active_tasks.discard(task)

    def _get_transport(self) -> GeminiTransport:
        """첫 실제 요청 때만 SDK 전송을 만든다."""
        if self._closed:
            raise LLMPermanentError("Gemini client is closed")
        if self._transport is None:
            try:
                self._transport = _SDKTransport(self._settings)
            except ValueError:
                raise LLMPermanentError("Invalid Gemini request configuration") from None
        return self._transport

    async def _request_once[T](self, operation: Callable[[GeminiTransport], Awaitable[T]]) -> T:
        """한 번 전송하고 알려진 외부 오류를 안전한 공통 오류로 변환한다."""
        transport = self._get_transport()
        try:
            return await operation(transport)
        except errors.APIError as error:
            raise _classify_api_error(error) from None
        # google-genai가 aiohttp를 쓰는 환경에서는 해당 예외도 고려해야 한다.
        except (httpx.TransportError, TimeoutError):
            raise LLMTransientError("Gemini transport temporarily unavailable") from None
        # 요청 설정 오류는 전송 어댑터가 구성 단계에서 영구 오류로 변환한다.
        # 응답 디코딩·검증 오류(JSONDecodeError, ValidationError 포함)는 ValueError다.
        # SDK generate_content 내부 ValueError의 요청/응답 발생 단계는 구분할 수 없다.
        except ValueError:
            raise LLMInvalidResponse("Gemini response parsing failed") from None

    async def _attempt(
        self,
        prompt: str,
        schema: JSONObject,
        image: Image | None,
        validator: validators.Draft202012Validator,
        attempt: int,
    ) -> tuple[JSONObject | None, Exception | None, LLMAttempt]:
        """한 번의 전송과 검증 결과를 수집한다. 취소는 그대로 전파한다."""
        response = GeminiResponse(None)
        result = None
        failure = None
        outcome: AttemptOutcome = "ok"
        started = monotonic()
        try:
            try:
                raw = await self._request_once(
                    lambda transport: transport.generate(prompt, schema, image)
                )
            finally:
                latency_ms = (monotonic() - started) * 1000
            response = GeminiResponse(raw) if isinstance(raw, str) else raw
            if response.text is None:
                raise LLMInvalidResponse("Gemini response text is invalid")
            result = _parse_response(response.text or "", validator)
        except (LLMInvalidResponse, LLMTransientError, LLMPermanentError) as error:
            failure = error
            outcome = _attempt_outcome(error)
        record = LLMAttempt(
            attempt,
            outcome,
            latency_ms,
            response.input_tokens,
            response.output_tokens,
            self._settings.gemini_model,
        )
        return result, failure, record

    async def _request(
        self,
        prompt: str,
        schema: JSONObject,
        image: Image | None,
        validator: validators.Draft202012Validator,
        on_attempt: Callable[[LLMAttempt], None] | None,
    ) -> JSONObject:
        """일시적 오류만 재시도한다. 횟수와 대기는 _RetryBudget이 정한다."""
        budget = _RetryBudget(self._settings)
        attempt = 0
        while True:
            attempt += 1
            async with self._semaphore:
                with self._active_request():
                    self._get_transport()
                    result, failure, record = await self._attempt(
                        prompt, schema, image, validator, attempt
                    )
            _notify_attempt(on_attempt, record)
            if not isinstance(failure, LLMTransientError):
                if failure is not None:
                    raise failure
                return cast(JSONObject, result)
            await budget.wait_or_raise(failure)

    async def ask_llm(
        self,
        prompt: str,
        schema: JSONObject,
        image: Image | None = None,
        *,
        on_attempt: Callable[[LLMAttempt], None] | None = None,
    ) -> JSONObject:
        """설정된 동시 호출 상한 안에서 응답을 받아 검증된 dict를 반환한다.

        Args:
            prompt: 비어 있지 않은 지시문.
            schema: Draft 2020-12 호환 응답 JSON Schema. 외부 참조는 지원하지 않는다.
            image: SDK에 전달할 PIL.Image.Image 또는 None.
            on_attempt: 완료된 전송 시도마다 호출할 동기 콜백. 오류는 재시도 없이 전파한다.

        Returns:
            스키마 검증을 통과한 JSON 객체.

        Raises:
            LLMPermanentError: 키, 입력 또는 설정이 잘못되었거나 종료된 경우.
            LLMQuotaExhausted: 하루 한도 429이거나, 어느 한도인지 모르는 429가 대기 뒤에도
                계속된 경우. LLMPermanentError의 하위 타입이다.
            LLMTransientError: 408·5xx·전송 오류가 세 번, 또는 분당 한도 429가
                Settings의 횟수·대기 상한까지 지속된 경우.
            LLMInvalidResponse: 응답 파싱 또는 검증이 실패한 경우.
        """
        try:
            _check_input(prompt, image)
            validator = _validator(schema)
            return await self._request(prompt, schema, image, validator, on_attempt)
        except (LLMInvalidResponse, LLMTransientError, LLMPermanentError) as error:
            failure = error
        _raise_detached(failure)

    async def _count_request(self, prompt: str) -> int:
        """생성과 슬롯·전송 오류 정책을 공유하며 일시적 오류만 재시도한다."""
        budget = _RetryBudget(self._settings)
        while True:
            try:
                async with self._semaphore:
                    with self._active_request():
                        total = await self._request_once(
                            lambda transport: self._count_once(transport, prompt)
                        )
                        return _validated_token_count(total)
            except LLMTransientError as failure:
                await budget.wait_or_raise(failure)

    async def _count_once(self, transport: GeminiTransport, prompt: str) -> int:
        """생성 전용 전송에는 명시적인 영구 오류를 반환한다."""
        if not callable(getattr(transport, "count_tokens", None)):
            raise LLMPermanentError("Gemini transport does not support token counting")
        return await cast(GeminiTokenTransport, transport).count_tokens(prompt)

    async def count_tokens(self, prompt: str) -> int:
        """현재 모델의 비음수 입력 토큰 수를 계산한다. 실제 생성 사용량은 아니다.

        빈 입력·종료는 LLMPermanentError, 잘못된 응답은 LLMInvalidResponse,
        세 번 지속된 일시적 오류는 LLMTransientError다. 취소는 그대로 전파한다.
        생성 전용 on_attempt에는 기록하지 않는다.
        """
        try:
            _check_input(prompt, None)
            return await self._count_request(prompt)
        except (LLMInvalidResponse, LLMTransientError, LLMPermanentError) as error:
            failure = error
        _raise_detached(failure)

    async def _close_transport(self) -> None:
        """종료 실패를 생성 오류와 분리하고 다시 종료할 수 있도록 연결을 남긴다."""
        if self._transport is None:
            return
        await _close_once(self._transport)
        self._transport = None

    async def aclose(self) -> None:
        """새 요청을 차단하고 진행 중 호출이 끝난 뒤 전송을 한 번 종료한다.

        활성 전송 Task 안에서 호출하면 LLMPermanentError이며 클라이언트는 닫히지 않는다.
        네트워크·SDK 종료 실패는 공통 오류로 전달하며 명시적으로 다시 종료할 수 있다.
        """
        if asyncio.current_task() in self._active_tasks:
            _raise_detached(
                LLMPermanentError("Cannot close Gemini client inside its active request")
            )
        async with self._close_lock:
            self._closed = True
            async with AsyncExitStack() as stack:
                for _ in range(self._max_concurrency):
                    await stack.enter_async_context(self._semaphore)
                with self._active_request():
                    await self._close_transport()
