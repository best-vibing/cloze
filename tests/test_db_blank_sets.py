"""blank_sets 테이블 확인. test_db와 같이 로컬에 DB가 없으면 건너뛰고, CI에서는 실패시킨다.

insert_blank_set·delete_blank_set은 test_db_blanks도 쓴다. blanks.set_id가 외래 키라
빈칸을 저장하려면 실제 세트 행(과 그 위의 사용자·문서 행)이 있어야 한다.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from cloze.config import load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.blank_sets import BlankSetRow, find_by_idempotency_key
from cloze.infra.db.blanks import BlankRow
from cloze.infra.db.documents import DocumentRow
from cloze.infra.db.users import UserRow


async def insert_blank_set(session: AsyncSession, key: str = "k1") -> uuid.UUID:
    """사용자·문서·세트 행을 하나씩 넣고 세트 ID를 돌려준다. 커밋하지 않는다."""
    # 비밀번호 해시는 scrypt라 느리다. 로그인하지 않으니 아무 문자열이나 넣는다.
    user = UserRow(login_id=f"test-{uuid.uuid4().hex[:8]}", password_hash="x", display_name="t")
    session.add(user)
    await session.flush()
    doc = DocumentRow(
        owner_id=user.id,
        sha256=uuid.uuid4().hex,
        filename="t.pdf",
        page_count=1,
        size_bytes=1,
        source_key="t",
    )
    session.add(doc)
    await session.flush()
    blank_set = BlankSetRow(
        document_id=doc.id,
        requested_by=user.id,
        idempotency_key=key,
        request_hash="h",
        preset_requested="auto",
        seed=1,
        progress_total=1,
    )
    session.add(blank_set)
    await session.flush()
    return blank_set.id


async def delete_blank_set(session: AsyncSession, set_id: uuid.UUID) -> None:
    """커밋한 세트를 지운다. 외래 키 순서대로 빈칸 → 세트 → 문서 → 사용자."""
    blank_set = await session.get(BlankSetRow, set_id)
    assert blank_set is not None
    doc_id, user_id = blank_set.document_id, blank_set.requested_by
    await session.execute(delete(BlankRow).where(BlankRow.set_id == set_id))
    await session.execute(delete(BlankSetRow).where(BlankSetRow.id == set_id))
    await session.execute(delete(DocumentRow).where(DocumentRow.id == doc_id))
    await session.execute(delete(UserRow).where(UserRow.id == user_id))
    await session.commit()


def _run(test: Callable[[AsyncSession], Awaitable[None]]) -> None:
    async def main() -> None:
        engine = make_engine(load_settings())
        try:
            async with make_sessionmaker(engine)() as session:
                await test(session)
        finally:
            await engine.dispose()

    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


def test_new_set_starts_queued_with_zero_progress() -> None:
    async def test(session: AsyncSession) -> None:
        set_id = await insert_blank_set(session)
        row = await session.scalar(select(BlankSetRow).where(BlankSetRow.id == set_id))
        assert row is not None
        await session.refresh(row)
        assert (row.status, row.progress_done, row.run_count) == ("queued", 0, 0)
        assert await find_by_idempotency_key(session, row.requested_by, "k1") == row

    _run(test)


def test_same_user_same_key_is_rejected() -> None:
    async def test(session: AsyncSession) -> None:
        set_id = await insert_blank_set(session)
        first = await session.get(BlankSetRow, set_id)
        assert first is not None
        session.add(
            BlankSetRow(
                document_id=first.document_id,
                requested_by=first.requested_by,
                idempotency_key="k1",
                request_hash="h",
                preset_requested="auto",
                seed=1,
                progress_total=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()

    _run(test)


def test_blank_needs_existing_set() -> None:
    async def test(session: AsyncSession) -> None:
        session.add(
            BlankRow(
                set_id=uuid.uuid4(),
                page_no=1,
                order=1,
                token_ids=[1],
                bbox=[0.0, 0.0, 1.0, 1.0],
                type="term",
                hint=None,
                style={},
                answer="a",
                variants=[],
                answer_group=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()

    _run(test)
