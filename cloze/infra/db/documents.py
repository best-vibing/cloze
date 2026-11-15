"""documents 테이블과 저장 함수. 필드는 docs/design/data-contract.md 4절 documents 행이다.

PDF 본문은 파일 저장소에, DB에는 위치·지문(sha256)·크기만 둔다(#28). 커밋은 호출하는 쪽이 한다.
"""

import asyncio
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from cloze.infra.db import Base
from cloze.infra.storage import LocalStorage, source_key


class DocumentRow(Base):
    __tablename__ = "documents"
    # 같은 사용자가 같은 PDF를 올리면 기존 문서를 쓴다. 삭제 표시한 문서는 빼고 따진다:
    # 지운 뒤 같은 PDF를 올리면 새 문서가 된다(계약 4절).
    __table_args__ = (
        Index(
            "uq_documents_owner_id_sha256_active",
            "owner_id",
            "sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    sha256: Mapped[str] = mapped_column(Text)
    # 사용자가 붙인 이름. 화면 표시용이고 저장 경로에는 쓰지 않는다.
    filename: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int]
    # 나중에 사용자별 저장 용량 한도를 둘 때 쓴다(#28 5번).
    size_bytes: Mapped[int]
    source_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 보관 기간이 정해지지 않아 비워 둔다(아키텍처 9.1 "보관 기간·자동 삭제").
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


async def find_active_document(
    session: AsyncSession, owner_id: uuid.UUID, sha256: str
) -> DocumentRow | None:
    result = await session.execute(
        select(DocumentRow).where(
            DocumentRow.owner_id == owner_id,
            DocumentRow.sha256 == sha256,
            DocumentRow.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def save_document(
    session: AsyncSession,
    storage: LocalStorage,
    owner_id: uuid.UUID,
    filename: str,
    pdf: bytes,
    page_count: int,
) -> DocumentRow:
    """원본을 저장하고 문서 행을 넣는다. 같은 사용자의 같은 PDF가 있으면 그 문서를 돌려준다.

    파일을 먼저 쓰고 행을 넣는다. 반대 순서면 "행은 있는데 파일이 없는" 문서가 생길 수 있다.
    호출하는 쪽이 커밋하지 않고 롤백하면 파일만 남는데, 그것은 정리 작업(#30)이 지운다.
    """
    sha256 = hashlib.sha256(pdf).hexdigest()
    existing = await find_active_document(session, owner_id, sha256)
    if existing is not None:
        return existing

    document_id = uuid.uuid4()
    key = source_key(document_id)
    await asyncio.to_thread(storage.put, key, pdf)
    document = DocumentRow(
        id=document_id,
        owner_id=owner_id,
        sha256=sha256,
        filename=filename,
        page_count=page_count,
        size_bytes=len(pdf),
        source_key=key,
    )
    try:
        async with session.begin_nested():
            session.add(document)
    except IntegrityError:
        # 같은 PDF가 거의 동시에 두 번 들어왔다(#28 4번). 고유 인덱스에서 늦은 쪽은
        # 먼저 들어간 쪽의 커밋을 기다린 뒤 여기로 온다. 자기 파일을 지우고 그 문서를 쓴다.
        await asyncio.to_thread(storage.delete, key)
        winner = await find_active_document(session, owner_id, sha256)
        if winner is None:
            raise
        return winner
    return document
