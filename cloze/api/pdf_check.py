"""업로드 검사(데이터 계약 3절, 신뢰성 설계 2·5절).

크기·시그니처는 요청 안에서 본다. 쪽수·암호화·JavaScript는 PDF를 파싱해야 하므로
제한(시간·메모리)을 건 하위 프로세스(cloze/infra/pdf_probe.py)에서 본다. 악성·손상 PDF가
파서를 멈추거나 메모리를 다 써도 API 프로세스는 살아남는다(아키텍처: 파서 격리).
"""

import asyncio
import json
import logging

# 고정된 인자로 검사 모듈만 실행한다(셸 없음, 사용자 입력은 표준 입력으로만).
import subprocess  # nosec B404
import sys
from typing import Any

from cloze.api.errors import ApiError
from cloze.config import Settings

logger = logging.getLogger(__name__)

# 하위 프로세스 결과 키 → 사용자에게 보일 메시지. 코드는 모두 encrypted_pdf다(계약 3절).
_REJECTED = {
    "encrypted": "암호가 걸린 PDF는 올릴 수 없어요.",
    "restricted": "복사·편집이 제한된 PDF는 올릴 수 없어요.",
    "javascript": "스크립트가 든 PDF는 올릴 수 없어요.",
}


async def check_pdf(pdf: bytes, settings: Settings) -> int:
    """검사를 통과하면 쪽수를 돌려준다. 걸리면 계약 3절의 ApiError.

    파싱은 하위 프로세스가 끝날 때까지 기다리므로(0.3~5초) 스레드에서 돌린다. 이벤트 루프에서
    바로 기다리면 그동안 같은 워커의 다른 요청이 모두 멈춘다(#101 리뷰). asyncio의 하위 프로세스
    API를 쓰지 않는 이유: Windows 개발 환경은 psycopg 때문에 SelectorEventLoop을 쓰는데,
    그 루프는 하위 프로세스를 지원하지 않는다.
    """
    if len(pdf) > settings.upload_max_bytes:
        raise ApiError("file_too_large", "파일이 너무 커요.")
    # PDF 표준은 %PDF- 앞에 쓰레기 바이트를 허용한다. 실제 리더들처럼 앞 1KB 안에서 찾는다.
    if b"%PDF-" not in pdf[:1024]:
        raise ApiError("not_pdf", "PDF 파일만 올릴 수 있어요.")
    found = await asyncio.to_thread(
        _probe, pdf, settings.upload_probe_timeout_s, settings.upload_probe_memory_mb
    )
    for key, message in _REJECTED.items():
        if found.get(key):
            raise ApiError("encrypted_pdf", message)
    pages = found.get("pages")
    if not isinstance(pages, int) or pages < 1:
        raise ApiError("not_pdf", "PDF를 읽을 수 없어요.")
    if pages > settings.upload_max_pages:
        raise ApiError("too_many_pages", f"{settings.upload_max_pages}쪽까지 올릴 수 있어요.")
    return pages


def _probe(pdf: bytes, timeout_s: float, memory_mb: int) -> dict[str, Any]:
    command = [sys.executable, "-I", "-m", "cloze.infra.pdf_probe", str(memory_mb)]
    try:
        # 시간이 넘으면 subprocess.run이 하위 프로세스를 kill한 뒤 TimeoutExpired를 낸다.
        done = subprocess.run(  # nosec B603
            command, input=pdf, capture_output=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        raise ApiError("not_pdf", "PDF를 읽는 데 너무 오래 걸려요.") from None
    if done.returncode != 0:
        # 손상된 PDF뿐 아니라 배포 문제(-I로 패키지를 못 찾음, 메모리 한도로 import 실패)도
        # 여기로 온다. 그러면 모든 업로드가 not_pdf가 되므로 원인을 로그에 남긴다.
        tail = done.stderr.decode(errors="replace").strip().splitlines()[-5:]
        logger.warning("PDF 검사 하위 프로세스 실패(%s): %s", done.returncode, " | ".join(tail))
        raise ApiError("not_pdf", "PDF를 읽을 수 없어요.")
    try:
        result = json.loads(done.stdout)
    except ValueError:
        logger.warning("PDF 검사 하위 프로세스 출력이 JSON이 아님: %r", done.stdout[:200])
        raise ApiError("not_pdf", "PDF를 읽을 수 없어요.") from None
    if not isinstance(result, dict):
        raise ApiError("not_pdf", "PDF를 읽을 수 없어요.")
    return result
