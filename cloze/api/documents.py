"""업로드 API(데이터 계약 2절 POST /documents, 아키텍처 5.1 1~2번).

검사 → 원본 저장 → 세트를 queued로 기록 → 작업 등록 → 202 {document_id, blank_set_id}.
"""

import hashlib
import logging
import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile

from cloze.api.auth import Db, current_user
from cloze.api.errors import ApiError
from cloze.api.pdf_check import check_pdf
from cloze.config import Settings
from cloze.infra.db.blank_sets import BlankSetRow, create_or_get_blank_set, find_by_idempotency_key
from cloze.infra.db.documents import save_document
from cloze.infra.db.users import UserRow
from cloze.infra.storage import LocalStorage

logger = logging.getLogger(__name__)
router = APIRouter()

Preset = Literal["auto", "vocab", "concept", "number", "definition"]
_DIFFERENT = "같은 요청 키로 다른 내용을 보냈어요."


async def enqueue(set_id: uuid.UUID) -> None:
    # 워커 모듈은 import할 때 브로커를 만든다. API를 import만 하는 테스트가 Redis 설정에
    # 묶이지 않게 쓸 때 가져온다.
    from cloze.worker import process_set

    await process_set.kiq(str(set_id))


def _accepted(blank_set: BlankSetRow, request_hash: str) -> dict[str, str]:
    if blank_set.request_hash != request_hash:
        raise ApiError("invalid_request", _DIFFERENT)
    return {"document_id": str(blank_set.document_id), "blank_set_id": str(blank_set.id)}


@router.post("/documents", status_code=202)
async def upload(
    request: Request,
    db: Db,
    user: Annotated[UserRow, Depends(current_user)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
    preset: Annotated[Preset, Form()] = "auto",
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    # 한도 + 1바이트까지만 읽는다. 큰 파일을 통째로 메모리에 올리지 않고도 넘었는지 안다.
    pdf = await file.read(settings.upload_max_bytes + 1)
    if len(pdf) > settings.upload_max_bytes:
        raise ApiError("file_too_large", "파일이 너무 커요.")
    sha256 = hashlib.sha256(pdf).hexdigest()
    request_hash = hashlib.sha256(f"{sha256}:{preset}".encode()).hexdigest()
    key = str(idempotency_key)

    # 중복 요청이면 검사·저장 없이 기존 세트를 돌려준다. 작업도 다시 등록하지 않는다:
    # 멈춘 세트의 재등록은 정리 작업 한 곳에서만 한다(신뢰성 설계 1.1).
    existing = await find_by_idempotency_key(db, user.id, key)
    if existing is not None:
        return _accepted(existing, request_hash)

    pages = await check_pdf(pdf, settings)
    filename = (file.filename or "document.pdf")[:255]
    storage = LocalStorage(settings.storage_dir)
    document = await save_document(db, storage, user.id, filename, pdf, pages)
    # 같은 PDF를 늦게 올린 요청이 고유 인덱스에서 기다리지 않게 바로 커밋한다(save_document 참고).
    await db.commit()

    new = BlankSetRow(
        document_id=document.id,
        requested_by=user.id,
        idempotency_key=key,
        request_hash=request_hash,
        preset_requested=preset,
        seed=secrets.randbelow(2**31),
        progress_total=pages,
    )
    blank_set = await create_or_get_blank_set(db, new)
    await db.commit()
    if blank_set is not new:  # 같은 키가 거의 동시에 두 번 왔다
        return _accepted(blank_set, request_hash)

    try:
        await enqueue(blank_set.id)
    except Exception:
        # 세트는 이미 queued로 커밋됐다. 멈춘 세트는 정리 작업(#30)이 다시 등록한다.
        logger.warning("세트 %s 작업 등록 실패, queued로 남김", blank_set.id, exc_info=True)
    return {"document_id": str(document.id), "blank_set_id": str(blank_set.id)}
