"""Gemini 429 응답 구조 확인(이슈 #120). 실제 요청을 보내므로 무료 한도를 조금 쓴다.

실행: uv run python -m scripts.gemini_429_probe [--max-requests 40] [--concurrency 8]

.env의 CLOZE_GEMINI_API_KEY·CLOZE_GEMINI_MODEL로 아주 짧은 요청을 1분 안에 연달아 보내고,
처음 받은 429의 code, status, details(RetryInfo·QuotaFailure 등)를 출력한다.
키, 프로젝트 번호·ID, 요청 헤더는 출력하지 않는다. 출력 전에 값과 키 이름으로 지운다.
"""

import argparse
import asyncio
import json
import re
from time import monotonic
from typing import Any

from google import genai
from google.genai import errors, types

from cloze.config import Settings

REDACTED = "<redacted>"
# 값에 프로젝트를 가리킬 수 있는 키는 통째로 지운다.
_SECRET_KEYS = re.compile(r"consumer|project|api_?key|key|header|service", re.IGNORECASE)
_SECRET_VALUES = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google API 키
    re.compile(r"projects/[^\s/\"',]+"),
    re.compile(r"project[_ ]?(?:number|id)?\s*[:=]?\s*\d{5,}", re.IGNORECASE),
    re.compile(r"\b\d{8,}\b"),  # 프로젝트 번호로 보이는 긴 숫자
]
# 값은 안전하지만 키 이름이 위 패턴과 겹치는 RetryInfo·QuotaFailure 필드.
_SAFE_KEYS = {"quotaId", "quotaMetric", "quotaDimensions", "quotaValue", "retryDelay"}


def _redact_text(text: str, key: str) -> str:
    for pattern in _SECRET_VALUES:
        text = pattern.sub(REDACTED, text)
    return text.replace(key, REDACTED) if key else text


def _redact(value: Any, key: str) -> Any:
    """dict·list를 따라가며 민감한 키는 값을 지우고 문자열 안의 키·번호를 가린다."""
    if isinstance(value, dict):
        return {
            k: REDACTED
            if k not in _SAFE_KEYS and _SECRET_KEYS.search(k) and k != "@type"
            else _redact(v, key)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        return _redact_text(value, key)
    return value


def _report(error: errors.APIError, key: str, sent: int, elapsed: float) -> None:
    print(f"\n=== {sent}번째 요청에서 {elapsed:.1f}초 만에 받은 오류 ===")
    print("code:", error.code)
    print("status:", _redact_text(str(error.status), key))
    print("details:")
    print(json.dumps(_redact(error.details, key), ensure_ascii=False, indent=2))


async def _probe(max_requests: int, concurrency: int) -> None:
    settings = Settings()
    if settings.gemini_api_key is None:
        raise SystemExit("CLOZE_GEMINI_API_KEY가 .env에 없다")
    key = settings.gemini_api_key.get_secret_value().strip()
    client = genai.Client(
        api_key=key,
        # SDK 내부 재시도를 꺼야 첫 429를 그대로 받는다.
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
    ).aio
    config = types.GenerateContentConfig(max_output_tokens=1)
    gate = asyncio.Semaphore(concurrency)
    found = asyncio.Event()
    counter = 0
    started = monotonic()

    async def one() -> None:
        nonlocal counter
        async with gate:
            if found.is_set():
                return
            counter += 1
            sent = counter
            try:
                await client.models.generate_content(
                    model=settings.gemini_model, contents="ok", config=config
                )
                print(f"{sent}: 성공")
            except errors.APIError as error:
                if error.code == 429 and not found.is_set():
                    found.set()
                    _report(error, key, sent, monotonic() - started)
                elif error.code != 429:
                    print(f"{sent}: {error.code} {_redact_text(str(error.status), key)}")

    try:
        await asyncio.gather(*(one() for _ in range(max_requests)))
    finally:
        await client.aclose()
    if not found.is_set():
        print(f"\n{counter}번 보냈지만 429가 나지 않았다. --max-requests를 늘려 다시 실행한다.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-requests", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    asyncio.run(_probe(args.max_requests, args.concurrency))


if __name__ == "__main__":
    main()
