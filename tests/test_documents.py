import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloze.config import load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.documents import DocumentRow, save_document
from cloze.infra.db.users import create_user
from cloze.infra.storage import LocalStorage, source_key

PDF = b"%PDF-1.7 fake body"


# --- 저장소 ---


def test_storage_put_read_delete(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    key = source_key(uuid.uuid4())
    storage.put(key, b"one")
    storage.put(key, b"two")  # 덮어쓰기
    assert storage.read(key) == b"two"
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["source.pdf"]  # 임시 파일 없음
    storage.delete(key)
    storage.delete(key)  # 없어도 오류 아님
    assert list(tmp_path.rglob("*")) == [tmp_path / "documents"]  # 문서 폴더도 지워짐


@pytest.mark.parametrize("key", ["../escape.pdf", "documents/../../x", "", "."])
def test_storage_rejects_keys_outside_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path / "root").put(key, b"x")


# --- documents 테이블 ---


def _with_db[T](work: Callable[[async_sessionmaker[AsyncSession]], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = make_engine(load_settings())
        try:
            return await work(make_sessionmaker(engine))
        finally:
            await engine.dispose()

    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        return asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


async def _user(sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with sessions() as session:
        user = await create_user(session, f"test-{uuid.uuid4().hex[:8]}", "pw", "사용자")
        await session.commit()
        return user.id


def test_same_pdf_reuses_document_until_deleted(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    async def work(sessions: async_sessionmaker[AsyncSession]) -> None:
        alice, bob = await _user(sessions), await _user(sessions)
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


def test_concurrent_same_pdf_keeps_one_row_and_one_file(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    async def work(sessions: async_sessionmaker[AsyncSession]) -> None:
        owner = await _user(sessions)
        async with sessions() as s1, sessions() as s2:
            first = await save_document(s1, storage, owner, "a.pdf", PDF, 1)  # 커밋 전
            second = asyncio.create_task(save_document(s2, storage, owner, "a.pdf", PDF, 1))
            await asyncio.sleep(0.5)
            assert not second.done()  # 고유 인덱스에서 s1의 커밋을 기다린다
            await s1.commit()
            winner = await second
            await s2.commit()
            assert winner.id == first.id
        async with sessions() as s:
            count = await s.scalar(
                select(func.count()).select_from(DocumentRow).where(DocumentRow.owner_id == owner)
            )
            assert count == 1
        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert files == [tmp_path / source_key(first.id)]  # 늦은 쪽 파일은 지워짐

    _with_db(work)
