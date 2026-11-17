"""blank_sets 테이블. 필드는 docs/design/data-contract.md 4절 blank_sets 행이다.

세트 상태의 유일한 기준이다. id가 곧 작업 ID다(ADR-006). 커밋은 호출하는 쪽이 한다.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from cloze.infra.db import Base


class BlankSetRow(Base):
    __tablename__ = "blank_sets"
    # 같은 사용자가 같은 요청 키로 다시 보내면 새로 만들지 않는다(계약 2절 중복 요청 방지).
    __table_args__ = (
        Index(
            "uq_blank_sets_requested_by_idempotency_key",
            "requested_by",
            "idempotency_key",
            unique=True,
        ),
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
