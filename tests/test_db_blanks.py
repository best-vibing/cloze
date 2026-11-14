"""save_page_blanks 확인. test_db와 같이 로컬에 DB가 없으면 건너뛰고, CI에서는 실패시킨다.

테스트는 커밋하지 않는다. 세션을 닫으면 롤백되므로 DB에 행이 남지 않는다.
동시 저장 테스트만 커밋하고, 끝나면 그 세트의 행을 지운다.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloze.config import load_settings
from cloze.core.models import Blank, BlankStyle
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.blanks import BlankRow, save_page_blanks

type Sessions = async_sessionmaker[AsyncSession]


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


def _blank(order: int, answer: str, **changes: object) -> Blank:
    blank = Blank(
        token_ids=[order * 10, order * 10 + 1],
        bbox=(0.115, 0.305, 0.295, 0.355),
        type="term",
        hint="세포 소기관",
        style=BlankStyle(font_size_ratio=0.025, font_color="#1a1a1a", bg_color="#ffffff"),
        answer=answer,
        variants=["mitochondrion"],
        answer_group=None,
        order=order,
    )
    return blank.model_copy(update=changes)


async def _page(session: AsyncSession, set_id: uuid.UUID, page_no: int) -> list[Blank]:
    rows = await session.scalars(
        select(BlankRow)
        .where(BlankRow.set_id == set_id, BlankRow.page_no == page_no)
        .order_by(BlankRow.order)
    )
    return [Blank.model_validate(row, from_attributes=True) for row in rows]


def test_saved_blanks_read_back_unchanged() -> None:
    set_id = uuid.uuid4()
    blanks = [
        _blank(1, "Mitochondria"),
        _blank(2, "42", type="number", hint=None, variants=[], answer_group=3),
    ]

    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            await save_page_blanks(session, set_id, 1, blanks)
            assert await _page(session, set_id, 1) == blanks
            ids = (
                await session.scalars(select(BlankRow.id).where(BlankRow.set_id == set_id))
            ).all()
            assert len(set(ids)) == 2

    _run(test)


def test_saving_same_page_again_keeps_only_second() -> None:
    set_id = uuid.uuid4()
    first = [_blank(1, "Mitochondria"), _blank(2, "Nucleus")]
    second = [_blank(1, "Ribosome")]

    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            await save_page_blanks(session, set_id, 1, first)
            await save_page_blanks(session, set_id, 1, second)
            assert await _page(session, set_id, 1) == second
            await save_page_blanks(session, set_id, 1, [])
            assert await _page(session, set_id, 1) == []

    _run(test)


def test_saving_one_page_leaves_other_pages_and_sets() -> None:
    set_id, other_set_id = uuid.uuid4(), uuid.uuid4()
    page_2 = [_blank(1, "Nucleus")]
    other_set = [_blank(1, "Golgi")]

    async def test(sessions: Sessions) -> None:
        async with sessions() as session:
            await save_page_blanks(session, set_id, 1, [_blank(1, "Mitochondria")])
            await save_page_blanks(session, set_id, 2, page_2)
            await save_page_blanks(session, other_set_id, 1, other_set)
            await save_page_blanks(session, set_id, 1, [_blank(1, "Ribosome")])
            assert await _page(session, set_id, 2) == page_2
            assert await _page(session, other_set_id, 1) == other_set

    _run(test)


def test_save_does_not_commit() -> None:
    set_id = uuid.uuid4()
    count = select(func.count()).select_from(BlankRow).where(BlankRow.set_id == set_id)

    async def test(sessions: Sessions) -> None:
        async with sessions() as session, sessions() as other:
            await save_page_blanks(session, set_id, 1, [_blank(1, "Mitochondria")])
            assert await session.scalar(count) == 1
            # 커밋했다면 다른 연결에서도 보인다.
            assert await other.scalar(count) == 0
            await session.rollback()
            assert await session.scalar(count) == 0

    _run(test)


async def _wait_until_lock_wait(sessions: Sessions, pid: int) -> None:
    async with sessions() as watcher:
        for _ in range(100):
            waiting = await watcher.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            if waiting:
                return
            # pg_stat_activity는 트랜잭션 안에서 처음 읽은 값을 계속 보여 준다.
            await watcher.rollback()
            await asyncio.sleep(0.05)
    raise AssertionError("둘째 저장이 첫째 저장의 커밋을 기다리지 않았다")


def test_concurrent_saves_of_same_page_leave_one_copy() -> None:
    # 재등록과 죽은 작업 회수가 겹치면 같은 세트를 두 워커가 동시에 저장한다(ADR-013).
    set_id = uuid.uuid4()
    first = [_blank(1, "A1"), _blank(2, "A2")]
    second = [_blank(1, "B1"), _blank(2, "B2")]

    async def test(sessions: Sessions) -> None:
        try:
            async with sessions() as worker_1, sessions() as worker_2:
                await save_page_blanks(worker_1, set_id, 1, first)
                pid = await worker_2.scalar(text("SELECT pg_backend_pid()"))
                saving = asyncio.create_task(save_page_blanks(worker_2, set_id, 1, second))
                try:
                    await _wait_until_lock_wait(sessions, pid)
                    await worker_1.commit()
                    with pytest.raises(IntegrityError):
                        await saving
                finally:
                    saving.cancel()
            async with sessions() as session:
                assert await _page(session, set_id, 1) == first
        finally:
            async with sessions() as session:
                await session.execute(delete(BlankRow).where(BlankRow.set_id == set_id))
                await session.commit()

    _run(test)
