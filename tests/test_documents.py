"""파일 저장소와 documents 테이블. DB 테스트는 로컬에 DB가 없으면 건너뛰고, CI에서는 실패시킨다.

DB 테스트는 만든 사용자·문서 행을 끝에 지운다(documents가 users를 외래 키로 가리켜 문서부터).
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloze.config import load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.documents import DocumentRow, save_document
from cloze.infra.db.users import UserRow
from cloze.infra.storage import LocalStorage, source_key

PDF = b"%PDF-1.7 fake body"

type Sessions = async_sessionmaker[AsyncSession]
type InsertUser = Callable[[AsyncSession], Awaitable[uuid.UUID]]
type LockWait = Callable[[Sessions, int], Awaitable[None]]


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


# --- 저장소 ---


def test_storage_put_read_delete(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    key = source_key(uuid.uuid4())
    storage.put(key, b"one")
    storage.put(key, b"two")  # 덮어쓰기
    assert storage.read(key) == b"two"
    assert [p.name for p in _files(tmp_path)] == ["source.pdf"]  # 임시 파일 없음
    storage.delete(key)
    storage.delete(key)  # 없어도 오류 아님
    assert _files(tmp_path) == []
    assert tmp_path.is_dir()  # 폴더는 지우지 않는다(빈 폴더 정리는 #30)


def test_storage_put_failure_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows에서 대상 파일이 열려 있거나 디스크가 꽉 차면 바꿔 끼우기가 실패한다.
    def fail(*_: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        LocalStorage(tmp_path).put(source_key(uuid.uuid4()), b"x")
    assert _files(tmp_path) == []


@pytest.mark.parametrize("key", ["../escape.pdf", "documents/../../x", "", "."])
def test_storage_rejects_keys_outside_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path / "root").put(key, b"x")


# --- documents 테이블 ---


def _with_db(work: Callable[[Sessions, list[uuid.UUID]], Awaitable[None]]) -> None:
    """work에 사용자 ID 목록을 넘긴다. work가 만든 사용자를 넣어 두면 끝에 문서와 함께 지운다."""

    async def run() -> None:
        engine = make_engine(load_settings())
        sessions = make_sessionmaker(engine)
        users: list[uuid.UUID] = []
        try:
            await work(sessions, users)
        finally:
            if users:
                async with sessions() as s:
                    await s.execute(delete(DocumentRow).where(DocumentRow.owner_id.in_(users)))
                    await s.execute(delete(UserRow).where(UserRow.id.in_(users)))
                    await s.commit()
            await engine.dispose()

    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


async def _users(
    sessions: Sessions, insert_user: InsertUser, users: list[uuid.UUID], count: int
) -> list[uuid.UUID]:
    async with sessions() as s:
        created = [await insert_user(s) for _ in range(count)]
        await s.commit()
    users.extend(created)
    return created


def test_same_pdf_reuses_document_until_deleted(tmp_path: Path, insert_user: InsertUser) -> None:
    storage = LocalStorage(tmp_path)

    async def work(sessions: Sessions, users: list[uuid.UUID]) -> None:
        alice, bob = await _users(sessions, insert_user, users, 2)
        async with sessions() as s:
            first = await save_document(s, storage, alice, "강의1.pdf", PDF, 3)
            again = await save_document(s, storage, alice, "다른이름.pdf", PDF, 3)
            other_user = await save_document(s, storage, bob, "강의1.pdf", PDF, 3)
            await s.commit()
            assert again.id == first.id and again.filename == "강의1.pdf"
            assert other_user.id != first.id
            assert first.size_bytes == len(PDF) and first.source_key == source_key(first.id)
            assert storage.read(first.source_key) == PDF

            first.deleted_at = datetime.now(UTC)
            await s.commit()
            fresh = await save_document(s, storage, alice, "강의1.pdf", PDF, 3)
            await s.commit()
            assert fresh.id != first.id  # 지운 뒤 같은 PDF는 새 문서

    _with_db(work)


def test_concurrent_same_pdf_keeps_one_row_and_one_file(
    tmp_path: Path, insert_user: InsertUser, wait_until_lock_wait: LockWait
) -> None:
    storage = LocalStorage(tmp_path)

    async def work(sessions: Sessions, users: list[uuid.UUID]) -> None:
        (owner,) = await _users(sessions, insert_user, users, 1)
        async with sessions() as s1, sessions() as s2:
            first = await save_document(s1, storage, owner, "a.pdf", PDF, 1)  # 커밋 전
            pid = await s2.scalar(text("SELECT pg_backend_pid()"))
            second = asyncio.create_task(save_document(s2, storage, owner, "a.pdf", PDF, 1))
            try:
                # 둘째가 고유 인덱스에서 s1의 커밋을 기다리는 것을 확인한 뒤 커밋한다.
                # 그래야 IntegrityError 분기(늦은 쪽이 자기 파일을 지움)를 실제로 지난다.
                await wait_until_lock_wait(sessions, pid)
                await s1.commit()
                winner = await second
            finally:
                second.cancel()
            await s2.commit()
            assert winner.id == first.id
        async with sessions() as s:
            count = await s.scalar(
                select(func.count()).select_from(DocumentRow).where(DocumentRow.owner_id == owner)
            )
            assert count == 1
        assert _files(tmp_path) == [tmp_path / source_key(first.id)]  # 늦은 쪽 파일은 지워짐

    _with_db(work)
