"""POST /documents: 업로드 → 검사 → 저장 → 세트 queued → 작업 등록(아키텍처 5.1 1~2번).

한도 검사(하루 쪽수·진행 중 세트·서비스 Gemini 상한)는 #51에서 이 흐름의 작업 등록 앞에 넣는다.
"""

import asyncio
import hashlib
import logging
import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from cloze.api.auth import Db, current_user
from cloze.api.errors import ApiError
from cloze.api.pdf_check import check_pdf
from cloze.config import Settings
from cloze.infra.db.blank_sets import BlankSetRow, find_by_idempotency_key
from cloze.infra.db.documents import save_document
from cloze.infra.db.users import UserRow

Preset = Literal["auto", "vocab", "concept", "number", "definition"]  # core.models.Policy와 같음

logger = logging.getLogger(__name__)
router = APIRouter()


class UploadResult(BaseModel):
    document_id: uuid.UUID
    blank_set_id: uuid.UUID


def _request_hash(sha256: str, preset: str) -> str:
    return hashlib.sha256(f"{sha256}:{preset}".encode()).hexdigest()


def _same_request(existing: BlankSetRow, request_hash: str) -> UploadResult:
    if existing.request_hash != request_hash:
        raise ApiError("invalid_request", "같은 요청 키로 다른 내용을 보냈어요.")
    return UploadResult(document_id=existing.document_id, blank_set_id=existing.id)


def _idempotency_key(value: str | None) -> str:
    # 클라이언트가 사용자 동작 한 번마다 UUID 하나를 만들어 보낸다(계약 2절).
    try:
        return str(uuid.UUID(value or ""))
    except ValueError:
        raise ApiError("invalid_request", "Idempotency-Key 헤더(UUID)가 필요해요.") from None


@router.post("/documents", status_code=202)
async def upload_document(
    request: Request,
    db: Db,
    user: Annotated[UserRow, Depends(current_user)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header()] = None,
    preset: Annotated[Preset, Form()] = "auto",
) -> UploadResult:
    settings: Settings = request.app.state.settings
    key = _idempotency_key(idempotency_key)
    # 한도보다 1바이트 더 읽어 "넘었다"만 안다. 전체 본문 크기 제한은 앞단(#60)에서 건다.
    pdf = await file.read(settings.upload_max_bytes + 1)
    request_hash = _request_hash(hashlib.sha256(pdf).hexdigest(), preset)

    existing = await find_by_idempotency_key(db, user.id, key)
    if existing is not None:
        return _same_request(existing, request_hash)  # 다시 보낸 요청: 검사·등록 없이 같은 답

    page_count = await asyncio.to_thread(check_pdf, pdf, settings)
    document = await save_document(
        db, request.app.state.storage, user.id, file.filename or "document.pdf", pdf, page_count
    )
    blank_set = BlankSetRow(
        document_id=document.id,
        requested_by=user.id,
        idempotency_key=key,
        request_hash=request_hash,
        preset_requested=preset,
        seed=secrets.randbelow(2**31),
        progress_total=page_count,
    )
    try:
        async with db.begin_nested():
            db.add(blank_set)
    except IntegrityError:
        # 같은 키가 거의 동시에 두 번 왔다. 먼저 들어간 세트를 돌려준다.
        winner = await find_by_idempotency_key(db, user.id, key)
        if winner is None:
            raise
        await db.commit()  # 문서 행은 재사용·새 문서 어느 쪽이든 남겨도 된다
        return _same_request(winner, request_hash)
    await db.commit()
    # 커밋한 뒤 등록한다. 등록이 실패해도 세트는 이미 저장됐으니 202로 답한다. 500을 주면
    # 같은 키로 다시 보내도 저장된 세트만 돌려받고 등록은 다시 하지 않는다.
    # queued로 남은 세트는 정리 작업이 다시 등록한다(ADR-011).
    try:
        await request.app.state.enqueue(blank_set.id)
    except Exception:
        logger.exception("작업 등록 실패, 정리 작업이 다시 등록한다: set_id=%s", blank_set.id)
    return UploadResult(document_id=document.id, blank_set_id=blank_set.id)
