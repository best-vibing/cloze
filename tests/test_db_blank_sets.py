"""blank_sets 테이블 확인. test_db와 같이 로컬에 DB가 없으면 건너뛰고, CI에서는 실패시킨다.

세트를 만드는 도우미(insert_blank_set·delete_blank_set)는 conftest의 fixture다.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloze.config import load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.blank_sets import BlankSetRow, create_or_get_blank_set, find_by_idempotency_key
from cloze.infra.db.blanks import BlankRow

type Sessions = async_sessionmaker[AsyncSession]
type InsertSet = Callable[..., Awaitable[uuid.UUID]]
type DeleteSet = Callable[[AsyncSession, uuid.UUID], Awaitable[None]]
type LockWait = Callable[[Sessions, int], Awaitable[None]]


def _run(test: Callable[[Sessions], Awaitable[None]]) -> None:
    async def main() -> None:
        engine = make_engine(load_settings())
        try:
            await test(make_sessionmaker(engine))
        finally:
            await engine.dispose()

    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


def _same_request(first: BlankSetRow, key: str = "k1") -> BlankSetRow:
    return BlankSetRow(
        document_id=first.document_id,
        requested_by=first.requested_by,
        idempotency_key=key,
        request_hash="h",
        preset_requested="auto",
        seed=2,
        progress_total=1,
    )


def test_new_set_starts_queued_with_zero_progress(insert_blank_set: InsertSet) -> None:
    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            set_id = await insert_blank_set(session)
            row = await session.get(BlankSetRow, set_id)
            assert row is not None
            await session.refresh(row)
            assert (row.status, row.progress_done, row.run_count) == ("queued", 0, 0)
            assert await find_by_idempotency_key(session, row.requested_by, "k1") == row

    _run(test)


def test_same_user_same_key_is_rejected(insert_blank_set: InsertSet) -> None:
    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            first = await session.get(BlankSetRow, await insert_blank_set(session))
            assert first is not None
            session.add(_same_request(first))
            with pytest.raises(IntegrityError):
                await session.flush()

    _run(test)


def test_status_outside_contract_is_rejected(insert_blank_set: InsertSet) -> None:
    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            row = await session.get(BlankSetRow, await insert_blank_set(session))
            assert row is not None
            row.status = "complete"  # 계약에 없는 이름
            with pytest.raises(IntegrityError, match="ck_blank_sets_status"):
                await session.flush()

    _run(test)


def test_create_or_get_returns_existing_for_same_key(insert_blank_set: InsertSet) -> None:
    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            first = await session.get(BlankSetRow, await insert_blank_set(session))
            assert first is not None
            assert await create_or_get_blank_set(session, _same_request(first)) is first
            fresh = _same_request(first, key="k2")
            assert await create_or_get_blank_set(session, fresh) is fresh

    _run(test)


def test_concurrent_same_key_both_get_one_set(
    insert_blank_set: InsertSet, delete_blank_set: DeleteSet, wait_until_lock_wait: LockWait
) -> None:
    # 중복 클릭·네트워크 재시도: 두 요청이 모두 조회에서 None을 받은 뒤 넣는다(#102 리뷰).
    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            set_id = await insert_blank_set(session, key="seed")
            await session.commit()
            seed = await session.get(BlankSetRow, set_id)
            assert seed is not None
        try:
            async with sessions() as s1, sessions() as s2:
                first = await create_or_get_blank_set(s1, _same_request(seed, key="dup"))
                pid = await s2.scalar(text("SELECT pg_backend_pid()"))
                second = asyncio.create_task(
                    create_or_get_blank_set(s2, _same_request(seed, key="dup"))
                )
                try:
                    await wait_until_lock_wait(sessions, pid)
                    await s1.commit()
                    winner = await second
                finally:
                    second.cancel()
                await s2.commit()
                assert winner.id == first.id
        finally:
            async with sessions() as session:
                await delete_blank_set(session, set_id)

    _run(test)


def test_updated_at_uses_wall_clock_within_transaction(insert_blank_set: InsertSet) -> None:
    # now()는 트랜잭션 시작 시각이라, 한 트랜잭션에서 여러 번 갱신하면 값이 멈춘다.
    # 정리 작업이 진행 중인 세트를 멈춘 세트로 볼 수 있다(ADR-011).
    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            row = await session.get(BlankSetRow, await insert_blank_set(session))
            assert row is not None
            row.progress_done = 1
            await session.flush()
            await session.refresh(row)
            first = row.updated_at
            await asyncio.sleep(0.05)
            row.progress_done = 2
            await session.flush()
            await session.refresh(row)
            assert row.updated_at > first

    _run(test)


def test_blank_needs_existing_set() -> None:
    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
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
