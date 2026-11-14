"""blanks 테이블과 저장 함수. 필드는 docs/design/data-contract.md 4절 blanks 행이다."""

import uuid
from typing import Any

from sqlalchemy import Float, Index, Integer, Text, delete, insert
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from cloze.core.models import Blank
from cloze.infra.db import Base


class BlankRow(Base):
    __tablename__ = "blanks"
    # 같은 쪽을 두 워커가 동시에 저장하면 둘째의 DELETE는 첫째의 커밋 전 행을 보지 못한다.
    # 유일 인덱스가 있으면 둘째는 첫째의 커밋을 기다린 뒤 UniqueViolation으로 실패한다.
    # 두 벌이 남지 않고, 실패한 작업은 재시도 때 blank_set_pages를 보고 그 쪽을 건너뛴다.
    __table_args__ = (
        Index("ix_blanks_set_id_page_no_order", "set_id", "page_no", "order", unique=True),
    )

    # ADR-006: 모든 ID는 무작위 UUID다. API의 blank_id가 이 값이다.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # blank_sets 테이블이 아직 없어 외래 키를 걸지 않는다. blank_sets를 만드는 PR에서 건다.
    set_id: Mapped[uuid.UUID]
    page_no: Mapped[int]
    order: Mapped[int]
    token_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    bbox: Mapped[list[float]] = mapped_column(ARRAY(Float))
    type: Mapped[str] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(Text)
    style: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # 서버 전용. 채점·정답 보기 밖에서는 내보내지 않는다(계약 4절).
    answer: Mapped[str] = mapped_column(Text)
    variants: Mapped[list[str]] = mapped_column(ARRAY(Text))
    answer_group: Mapped[int | None]


async def save_page_blanks(
    session: AsyncSession, set_id: uuid.UUID, page_no: int, blanks: list[Blank]
) -> None:
    """한 쪽의 빈칸을 지우고 다시 넣는다. 커밋은 호출하는 쪽(워커)이 한다(reliability 3절 2·5)."""
    await session.execute(
        delete(BlankRow).where(BlankRow.set_id == set_id, BlankRow.page_no == page_no)
    )
    if not blanks:
        return
    rows = [
        {"set_id": set_id, "page_no": page_no, **blank.model_dump(mode="json")} for blank in blanks
    ]
    await session.execute(insert(BlankRow), rows)
