"""pages·blank_set_pages 테이블. 필드는 docs/design/data-contract.md 4절이다.

#53(세트 조회)이 쪽 크기와 가린 이미지 키를 읽으려고 먼저 만든다. 행을 쓰는 쪽은 워커(#52)다.
커밋은 호출하는 쪽이 한다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, ForeignKeyConstraint, Index, Text, and_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from cloze.infra.db import Base
from cloze.infra.db.blank_sets import BlankSetRow
from cloze.infra.db.blanks import BlankRow
from cloze.infra.db.documents import DocumentRow


class PageRow(Base):
    """문서의 한 쪽. 같은 추출 버전이면 세트끼리 재사용한다."""

    __tablename__ = "pages"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    page_no: Mapped[int] = mapped_column(primary_key=True)
    extract_version: Mapped[int] = mapped_column(primary_key=True)
    # PDF 쪽 크기(pt). 화면이 bbox(0~1)를 이 비율로 그린다.
    width_pt: Mapped[float] = mapped_column(Float)
    height_pt: Mapped[float] = mapped_column(Float)
    # 토큰을 어디서 얻었는지(텍스트 레이어 또는 OCR).
    source: Mapped[str] = mapped_column(Text)
    # 원본 쪽 이미지. 정답이 그대로 보이므로 /images로 내보내지 않는다.
    image_key: Mapped[str] = mapped_column(Text)
    tokens_key: Mapped[str] = mapped_column(Text)


class BlankSetPageRow(Base):
    """세트의 한 쪽 결과. 행이 있으면 그 쪽은 처리가 끝났고 가린 이미지가 있다.

    실패한 쪽은 행이 없다. 그래서 세트 조회의 pages는 이 행들만 보면 된다(계약 2절 partial).
    """

    __tablename__ = "blank_set_pages"
    __table_args__ = (
        # /images/{key}가 키로 세트를 찾는다. 키는 세트·쪽마다 하나라 고유하다.
        Index("uq_blank_set_pages_image_key", "image_key", unique=True),
        ForeignKeyConstraint(["set_id"], ["blank_sets.id"]),
    )

    set_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    page_no: Mapped[int] = mapped_column(primary_key=True)
    # 가린 이미지(WEBP). 이 키만 /images로 내보낸다.
    image_key: Mapped[str] = mapped_column(Text)
    # 빈칸을 고른 쪽(gemini 또는 rule).
    selection_source: Mapped[str] = mapped_column(Text)
    quality: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# --- 세트 조회(#53)용 읽기. 정답 열(answer·variants·answer_group)과 token_ids는 고르지 않는다. ---

# 화면에 보내도 되는 빈칸 열만. BlankRow 전체를 고르지 않아 정답이 메모리에도 올라오지 않는다.
_PUBLIC_BLANK_COLUMNS = (
    BlankRow.id,
    BlankRow.order,
    BlankRow.bbox,
    BlankRow.type,
    BlankRow.hint,
    BlankRow.style,
)


@dataclass
class Owner:
    """리소스를 내보내도 되는지 판단할 정보. 판단과 로그는 API가 한다(ADR-006)."""

    owner_id: uuid.UUID
    deleted_at: datetime | None


@dataclass
class PageView:
    page_no: int
    image_key: str
    width_pt: float
    height_pt: float
    blanks: list[dict[str, Any]] = field(default_factory=list)


async def find_blank_set(
    session: AsyncSession, set_id: uuid.UUID
) -> tuple[BlankSetRow, Owner] | None:
    """세트와 그 문서의 소유자·삭제 표시. 쿼리 하나."""
    result = await session.execute(
        select(BlankSetRow, DocumentRow.owner_id, DocumentRow.deleted_at)
        .join(DocumentRow, DocumentRow.id == BlankSetRow.document_id)
        .where(BlankSetRow.id == set_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return row[0], Owner(row[1], row[2])


async def list_page_views(session: AsyncSession, blank_set: BlankSetRow) -> list[PageView]:
    """끝난 쪽(blank_set_pages 행)과 그 쪽 크기·빈칸. 쪽·빈칸을 한 쿼리로 읽는다(폴링 비용).

    쪽 크기는 세트가 쓴 추출 버전의 pages 행에서 읽는다. 빈칸이 없는 쪽도 빈 목록으로 나온다.
    """
    page = and_(
        PageRow.document_id == blank_set.document_id,
        PageRow.page_no == BlankSetPageRow.page_no,
        PageRow.extract_version == blank_set.extract_version,
    )
    blank = and_(
        BlankRow.set_id == BlankSetPageRow.set_id, BlankRow.page_no == BlankSetPageRow.page_no
    )
    result = await session.execute(
        select(
            BlankSetPageRow.page_no,
            BlankSetPageRow.image_key,
            PageRow.width_pt,
            PageRow.height_pt,
            *_PUBLIC_BLANK_COLUMNS,
        )
        .join(PageRow, page)
        .outerjoin(BlankRow, blank)
        .where(BlankSetPageRow.set_id == blank_set.id)
        .order_by(BlankSetPageRow.page_no, BlankRow.order)
    )
    views: dict[int, PageView] = {}
    for row in result:
        view = views.setdefault(
            row.page_no, PageView(row.page_no, row.image_key, row.width_pt, row.height_pt)
        )
        if row.id is not None:  # 바깥 조인이라 빈칸 없는 쪽은 빈칸 열이 NULL이다
            view.blanks.append(
                {
                    "blank_id": row.id,
                    "order": row.order,
                    "bbox": row.bbox,
                    "type": row.type,
                    "hint": row.hint,
                    "style": row.style,
                }
            )
    return list(views.values())


async def find_image_owner(session: AsyncSession, image_key: str) -> Owner | None:
    """가린 이미지 키의 소유자. blank_set_pages에 있는 키만 찾으므로 원본 키는 None이다."""
    result = await session.execute(
        select(DocumentRow.owner_id, DocumentRow.deleted_at)
        .select_from(BlankSetPageRow)
        .join(BlankSetRow, BlankSetRow.id == BlankSetPageRow.set_id)
        .join(DocumentRow, DocumentRow.id == BlankSetRow.document_id)
        .where(BlankSetPageRow.image_key == image_key)
    )
    row = result.one_or_none()
    return None if row is None else Owner(row[0], row[1])
