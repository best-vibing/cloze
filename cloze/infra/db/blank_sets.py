"""blank_sets 테이블. 필드는 docs/design/data-contract.md 4절 blank_sets 행이다.

세트 상태의 유일한 기준이다. id가 곧 작업 ID다(ADR-006). 커밋은 호출하는 쪽이 한다.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from cloze.infra.db import Base

# 계약 2절 status 표. 워커가 오타나 계약에 없는 이름을 쓰면 DB가 바로 막는다.
STATUSES = ("queued", "running", "done", "partial", "failed")


class BlankSetRow(Base):
    __tablename__ = "blank_sets"
    __table_args__ = (
        # 같은 사용자가 같은 요청 키로 다시 보내면 새로 만들지 않는다(계약 2절 중복 요청 방지).
        Index(
            "uq_blank_sets_requested_by_idempotency_key",
            "requested_by",
            "idempotency_key",
            unique=True,
        ),
        # 문서별 세트 조회와, 문서를 지울 때의 외래 키 검사가 순차 스캔이 되지 않게 한다.
        Index("ix_blank_sets_document_id", "document_id"),
        CheckConstraint(f"status IN {STATUSES}", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(Text)
    # 요청 내용의 지문(파일 sha256 + 방식). 같은 키에 다른 내용이 오면 거절하는 데 쓴다.
    request_hash: Mapped[str] = mapped_column(Text)
    preset_requested: Mapped[str] = mapped_column(Text)
    # 아래 다섯은 워커가 채운다(자동 모드 분석 결과, 기록용 버전).
    preset_applied: Mapped[str | None] = mapped_column(Text)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    extract_version: Mapped[int | None]
    seed: Mapped[int]
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    # 계약의 progress {done, total}. 실패한 쪽도 done으로 센다.
    progress_done: Mapped[int] = mapped_column(default=0, server_default="0")
    progress_total: Mapped[int]
    run_count: Mapped[int] = mapped_column(default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 진행할 때마다 갱신. 정리 작업이 멈춘 세트를 찾는 기준이다(ADR-011).
    # now()는 트랜잭션 시작 시각이라, 여러 쪽을 한 트랜잭션에서 처리하면 값이 멈춰 보인다.
    # 갱신할 때는 실제 시각(clock_timestamp)을 쓴다.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.clock_timestamp()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


async def find_by_idempotency_key(
    session: AsyncSession, requested_by: uuid.UUID, key: str
) -> BlankSetRow | None:
    result = await session.execute(
        select(BlankSetRow).where(
            BlankSetRow.requested_by == requested_by, BlankSetRow.idempotency_key == key
        )
    )
    return result.scalar_one_or_none()


async def create_or_get_blank_set(session: AsyncSession, new: BlankSetRow) -> BlankSetRow:
    """새 세트를 넣는다. 같은 사용자·같은 요청 키의 세트가 이미 있으면 그것을 돌려준다.

    돌려받은 행이 `new`가 아니면 중복 요청이다. 호출한 쪽은 `request_hash`를 비교해서
    다르면 invalid_request로 거절한다(계약 2절).

    같은 키가 거의 동시에 두 번 오면(중복 클릭, 네트워크 재시도) 둘 다 조회에서 None을 받는다.
    늦은 쪽은 고유 인덱스에서 먼저 넣은 쪽의 커밋을 기다린 뒤 IntegrityError로 실패하는데,
    savepoint만 되돌리고 다시 조회해 먼저 들어간 세트를 돌려준다(#100 save_document와 같은 방식).
    """
    existing = await find_by_idempotency_key(session, new.requested_by, new.idempotency_key)
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            session.add(new)
    except IntegrityError:
        winner = await find_by_idempotency_key(session, new.requested_by, new.idempotency_key)
        if winner is None:
            raise  # 고유 인덱스가 아닌 다른 제약(없는 문서 등)에 걸렸다
        return winner
    return new
