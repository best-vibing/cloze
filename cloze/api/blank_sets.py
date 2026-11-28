"""세트 조회와 가린 이미지(데이터 계약 2절 `GET /blank-sets/{id}`, `GET /images/{key}`).

응답은 아래 Pydantic 모델의 필드만 나간다. 정답(answer·variants·answer_group)과 token_ids는
모델에 없고, DB에서도 읽지 않는다(cloze/infra/db/pages.py).

없는 세트, 남의 세트, 삭제 표시한 문서의 세트는 모두 같은 not_found다.
이유는 로그에만 남긴다(ADR-006).
"""

import asyncio
import logging
import time
import uuid
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

from cloze.api.auth import Db, current_user
from cloze.api.errors import ApiError
from cloze.config import Settings
from cloze.infra import signed_url
from cloze.infra.db.blank_sets import BlankSetRow
from cloze.infra.db.pages import Owner, PageView, find_blank_set, find_image_owner, list_page_views
from cloze.infra.db.users import UserRow
from cloze.infra.storage import LocalStorage

logger = logging.getLogger(__name__)
router = APIRouter()

User = Annotated[UserRow, Depends(current_user)]

# 가린 이미지는 WEBP다(cloze/core/redact.py encode_webp). 다른 확장자는 내보내지 않는다.
IMAGE_TYPES = {".webp": "image/webp"}

# failed인데 error_message가 비어 있을 때 쓰는 문장(계약 2절 error.code 표).
FAILED_MESSAGES = {
    "unreadable_pdf": "인식이 잘 안 됐어요, 다시 올려 주세요.",
    "processing_failed": "만들지 못했어요. 잠시 뒤 다시 만들어 주세요.",
    "document_deleted": "삭제된 문서예요.",
}


def _not_found() -> ApiError:
    return ApiError("not_found", "찾을 수 없어요.")


# --- 응답 모델(계약 2절 예시와 같은 모양) ---


class StyleOut(BaseModel):
    font_size_ratio: float
    font_color: str
    bg_color: str


class BlankOut(BaseModel):
    blank_id: uuid.UUID
    order: int
    bbox: list[float]
    type: str
    hint: str | None
    style: StyleOut


class PageOut(BaseModel):
    page_no: int
    image_url: str
    width_pt: float
    height_pt: float
    blanks: list[BlankOut]


class ProgressOut(BaseModel):
    done: int
    total: int


class SetErrorOut(BaseModel):
    code: str
    message: str


class BlankSetOut(BaseModel):
    blank_set_id: uuid.UUID
    # 다시 만들기(POST /documents/{id}/blank-sets)에 쓴다(#118). 모든 상태에 넣는다.
    document_id: uuid.UUID
    status: Literal["queued", "running", "done", "partial", "failed"]
    progress: ProgressOut
    preset_requested: str
    preset_applied: str | None
    preset_reason: str | None
    pages: list[PageOut]
    error: SetErrorOut | None = None

    @model_serializer(mode="wrap")
    def _error_only_when_failed(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # 계약 예시는 failed일 때만 error 키가 있다. 다른 상태에서 "error": null을 내지 않는다.
        data: dict[str, Any] = handler(self)
        if data.get("error") is None:
            data.pop("error", None)
        return data


# --- 도우미 ---


def _signing_key(request: Request) -> bytes:
    # create_app이 설정을 확인하고 넣어 둔다(없으면 앱이 뜨지 않는다).
    key: bytes = request.app.state.image_signing_key
    return key


def _visible(owner: Owner | None, user: UserRow, what: str) -> bool:
    if owner is None:
        logger.info("%s 없음", what)
        return False
    if owner.owner_id != user.id:
        logger.info("%s 남의 것(요청 사용자 %s)", what, user.id)
        return False
    if owner.deleted_at is not None:
        logger.info("%s 문서 삭제 표시됨", what)
        return False
    return True


def _preset_reason(blank_set: BlankSetRow) -> str | None:
    # 자동 모드 분석 결과(cloze/core/analyze.py)의 reason. 분석 전이거나 방식을 직접 골랐으면 없다.
    analysis = blank_set.analysis or {}
    reason = analysis.get("reason")
    return reason if isinstance(reason, str) else None


def _error(blank_set: BlankSetRow) -> SetErrorOut:
    code = blank_set.error_code or "processing_failed"
    message = blank_set.error_message or FAILED_MESSAGES.get(
        code, FAILED_MESSAGES["processing_failed"]
    )
    return SetErrorOut(code=code, message=message)


def _page_out(view: PageView, key: bytes, now: float, ttl_s: int) -> PageOut:
    return PageOut(
        page_no=view.page_no,
        image_url=signed_url.sign(view.image_key, key, now, ttl_s),
        width_pt=view.width_pt,
        height_pt=view.height_pt,
        blanks=[BlankOut.model_validate(blank) for blank in view.blanks],
    )


# --- 라우트 ---


@router.get("/blank-sets/{set_id}", response_model=BlankSetOut)
async def get_blank_set(set_id: str, request: Request, user: User, db: Db) -> BlankSetOut:
    try:
        parsed = uuid.UUID(set_id)
    except ValueError:
        raise _not_found() from None  # 모양이 틀린 ID도 없는 세트와 같게 답한다
    found = await find_blank_set(db, parsed)
    if found is None or not _visible(found[1], user, f"세트 {parsed}"):
        raise _not_found()
    blank_set = found[0]

    pages: list[PageOut] = []
    if blank_set.status in ("done", "partial"):
        settings: Settings = request.app.state.settings
        key, now = _signing_key(request), time.time()
        views = await list_page_views(db, blank_set)
        pages = [_page_out(view, key, now, settings.image_url_ttl_s) for view in views]

    return BlankSetOut(
        blank_set_id=blank_set.id,
        document_id=blank_set.document_id,
        status=blank_set.status,  # type: ignore[arg-type]  # DB CHECK 제약이 다섯 값만 받는다
        progress=ProgressOut(done=blank_set.progress_done, total=blank_set.progress_total),
        preset_requested=blank_set.preset_requested,
        preset_applied=blank_set.preset_applied,
        preset_reason=_preset_reason(blank_set),
        pages=pages,
        error=_error(blank_set) if blank_set.status == "failed" else None,
    )


@router.get("/images/{key:path}")
async def get_image(
    key: str,
    request: Request,
    user: User,
    db: Db,
    exp: str | None = None,
    sig: str | None = None,
) -> Response:
    """서명·만료 → 소유자·삭제 표시 → 파일 순으로 확인한다. 어느 것이 틀려도 같은 not_found."""
    now = time.time()
    expires = None if exp is None else signed_url.parse_exp(exp)
    if expires is None or sig is None:
        raise _not_found()
    if not signed_url.verify(key, expires, sig, _signing_key(request), now):
        logger.info("이미지 서명이 틀리거나 만료됨")
        raise _not_found()
    media_type = IMAGE_TYPES.get(PurePosixPath(key).suffix)
    # blank_set_pages에 있는 키만 찾으므로 원본 PDF·원본 쪽 이미지 키는 여기서 걸러진다.
    if media_type is None or not _visible(await find_image_owner(db, key), user, "이미지"):
        raise _not_found()

    storage: LocalStorage = request.app.state.storage
    try:
        data = await asyncio.to_thread(storage.read, key)
    except (FileNotFoundError, ValueError):
        logger.warning("이미지 파일 없음: %s", key)
        raise _not_found() from None

    # 브라우저 캐시는 주소가 만료되기 전까지만. 남과 공유하는 캐시(프록시)에는 두지 않는다.
    max_age = max(0, expires - int(now))
    headers = {"Cache-Control": f"private, max-age={max_age}", "X-Content-Type-Options": "nosniff"}
    return Response(content=data, media_type=media_type, headers=headers)
