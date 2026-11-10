"""설정 기반 Gemini 비동기 호출과 응답 검증."""

import asyncio
import json
import math
import secrets
from contextlib import AsyncExitStack
from typing import Any, Protocol, cast

import httpx
from google import genai
from google.genai import errors, types
from jsonschema import exceptions, validators
from PIL.Image import Image
from referencing import Registry
from referencing.exceptions import Unresolvable

from cloze.config import Settings
from cloze.core.errors import LLMInvalidResponse, LLMPermanentError, LLMTransientError

type JSONObject = dict[str, Any]


class GeminiTransport(Protocol):
    """실제 SDK와 오프라인 테스트가 공유하는 전송 경계."""

    async def generate(self, prompt: str, schema: JSONObject, image: Image | None) -> str:
        """요청을 전송하고 JSON 응답 문자열을 반환한다."""
        ...

    async def aclose(self) -> None:
        """전송 자원을 해제한다."""
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
        try:
            key = settings.gemini_api_key_file.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError):
            raise LLMPermanentError("Gemini API key file cannot be read") from None
        if not key:
            raise LLMPermanentError("Gemini API key file must not be empty")
        self._client = genai.Client(api_key=key, http_options=_http_options(settings)).aio
        self._model = settings.gemini_model

    async def generate(self, prompt: str, schema: JSONObject, image: Image | None) -> str:
        """선택적 PIL 이미지와 JSON Schema를 SDK에 전달한다.

        Args:
            prompt: 호출자가 구성한 지시문.
            schema: 응답 객체의 JSON Schema.
            image: 메모리에 있는 PIL 이미지 또는 None.

        Returns:
            응답 JSON 문자열.
        """
        contents: list[types.PartUnion] = [prompt]
        if image is not None:
            contents.append(image)
        response = await self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_json_schema=schema
            ),
        )
        return response.text or ""

    async def aclose(self) -> None:
        """SDK의 비동기 HTTP 연결을 해제한다."""
        await self._client.aclose()


def _classify_api_error(error: errors.APIError) -> Exception:
    """외부 오류 본문을 노출하지 않고 상태 코드만 분류한다."""
    if error.code in {408, 429} or 500 <= error.code < 600:
        return LLMTransientError("Gemini service temporarily unavailable")
    return LLMPermanentError("Gemini authentication, model or request configuration failed")


def _validator(schema: JSONObject) -> validators.Draft202012Validator:
    """로컬 스키마를 확인하고 외부 참조를 다운로드하지 않는 검증기를 만든다."""
    try:
        validators.Draft202012Validator.check_schema(schema)
    except exceptions.SchemaError:
        raise LLMPermanentError("Invalid response JSON Schema") from None
    return validators.Draft202012Validator(schema, registry=Registry())


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


def _check_input(prompt: str, image: Image | None) -> None:
    """지시문과 이미지의 실행 시점 타입 및 빈 지시문을 검사한다."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise LLMPermanentError("Gemini prompt must not be empty")
    if image is not None and not isinstance(image, Image):
        raise LLMPermanentError("Gemini image must be a PIL.Image.Image")


class GeminiClient:
    """하나의 이벤트 루프에서 재사용할 호출 제한과 전송 수명을 관리한다.

    여러 호출은 같은 인스턴스를 공유해야 동시 상한 4가 적용된다.
    종료는 진행 중 호출을 기다리며, 종료가 취소되면 aclose를 다시 호출할 수 있다.
    """

    def __init__(
        self, settings: Settings | None = None, *, transport: GeminiTransport | None = None
    ) -> None:
        """설정과 선택적 테스트 전송을 보관한다. 키는 아직 검사하지 않는다."""
        self._settings = settings if settings is not None else Settings()
        self._transport = transport
        self._semaphore = asyncio.Semaphore(4)
        self._close_lock = asyncio.Lock()
        self._closed = False

    def _get_transport(self) -> GeminiTransport:
        """첫 실제 요청 때만 SDK 전송을 만든다."""
        if self._transport is None:
            self._transport = _SDKTransport(self._settings)
        return self._transport

    async def _request_once(self, prompt: str, schema: JSONObject, image: Image | None) -> str:
        """한 번 전송하고 알려진 외부 오류를 안전한 공통 오류로 변환한다."""
        try:
            return await self._get_transport().generate(prompt, schema, image)
        except errors.APIError as error:
            raise _classify_api_error(error) from None
        except (httpx.TransportError, TimeoutError):
            raise LLMTransientError("Gemini transport temporarily unavailable") from None
        except ValueError:
            raise LLMPermanentError("Invalid Gemini request configuration") from None

    async def _request(self, prompt: str, schema: JSONObject, image: Image | None) -> str:
        """일시적 오류만 최초 호출 포함 최대 세 번 시도한다."""
        for attempt in range(3):
            try:
                return await self._request_once(prompt, schema, image)
            except LLMTransientError:
                if attempt == 2:
                    raise LLMTransientError("Gemini request failed after 3 attempts") from None
                await _retry_delay(attempt)
        raise AssertionError("Unreachable retry state")

    async def ask_llm(
        self, prompt: str, schema: JSONObject, image: Image | None = None
    ) -> JSONObject:
        """최대 네 호출 안에서 응답을 받아 검증된 dict를 반환한다.

        Args:
            prompt: 비어 있지 않은 지시문.
            schema: Draft 2020-12 호환 응답 JSON Schema. 외부 참조는 지원하지 않는다.
            image: SDK에 전달할 PIL.Image.Image 또는 None.

        Returns:
            스키마 검증을 통과한 JSON 객체.

        Raises:
            LLMPermanentError: 키, 입력 또는 설정이 잘못되었거나 종료된 경우.
            LLMTransientError: 일시적 오류가 세 번 지속된 경우.
            LLMInvalidResponse: 응답 파싱 또는 검증이 실패한 경우.
        """
        _check_input(prompt, image)
        validator = _validator(schema)
        async with self._semaphore:
            if self._closed:
                raise LLMPermanentError("Gemini client is closed")
            raw = await self._request(prompt, schema, image)
            return _parse_response(raw, validator)

    async def aclose(self) -> None:
        """새 요청을 차단하고 진행 중 호출이 끝난 뒤 전송을 한 번 종료한다."""
        async with self._close_lock:
            self._closed = True
            async with AsyncExitStack() as stack:
                for _ in range(4):
                    await stack.enter_async_context(self._semaphore)
                if self._transport is not None:
                    await self._transport.aclose()
                    self._transport = None
