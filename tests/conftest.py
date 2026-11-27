"""여러 테스트 파일이 같이 쓰는 fixture.

DB 도우미는 함수를 돌려주는 fixture로 둔다. 테스트 파일끼리 `from test_x import ...`나
`from conftest import ...`로 가져오면 pytest가 tests/를 sys.path에 넣어 줄 때만 동작한다
(`--import-mode=importlib`에서 실패, #102 리뷰).
"""

import asyncio
import socket
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google import genai
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloze.infra.db.blank_sets import BlankSetRow
from cloze.infra.db.blanks import BlankRow
from cloze.infra.db.documents import DocumentRow
from cloze.infra.db.users import UserRow


@pytest.fixture
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """설정과 작업 폴더를 격리하고 실제 SDK 및 네트워크 호출을 막는다."""
    monkeypatch.chdir(tmp_path)
    for suffix in (
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_TIMEOUT_S",
        "GEMINI_MAX_CONCURRENCY",
        "SAMPLES_DIR",
    ):
        monkeypatch.delenv(f"CLOZE_{suffix}", raising=False)
    blocked = MagicMock(side_effect=AssertionError("Unexpected network or SDK access"))
    monkeypatch.setattr(genai, "Client", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


# --- DB 도우미 ---


async def _wait_until_lock_wait(sessions: async_sessionmaker[AsyncSession], pid: int) -> None:
    """연결 pid가 다른 트랜잭션의 잠금을 기다리기 시작할 때까지 기다린다(최대 5초)."""
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
    raise AssertionError("둘째 연결이 첫째 트랜잭션의 커밋을 기다리지 않았다")


async def _insert_user(session: AsyncSession) -> uuid.UUID:
    """사용자 행을 넣고 ID를 돌려준다. 커밋하지 않는다.

    비밀번호 해시는 scrypt라 계정마다 1~2초 걸린다. 로그인하지 않으니 아무 문자열이나 넣는다.
    """
    user = UserRow(login_id=f"test-{uuid.uuid4().hex[:8]}", password_hash="x", display_name="t")
    session.add(user)
    await session.flush()
    return user.id


async def _insert_blank_set(session: AsyncSession, key: str = "k1") -> uuid.UUID:
    """사용자·문서·세트 행을 하나씩 넣고 세트 ID를 돌려준다. 커밋하지 않는다.

    blanks.set_id가 외래 키라 빈칸을 저장하려면 실제 세트 행(과 그 위의 사용자·문서 행)이
    있어야 한다.
    """
    user_id = await _insert_user(session)
    doc = DocumentRow(
        owner_id=user_id,
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
        requested_by=user_id,
        idempotency_key=key,
        request_hash="h",
        preset_requested="auto",
        seed=1,
        progress_total=1,
    )
    session.add(blank_set)
    await session.flush()
    return blank_set.id


async def _delete_blank_set(session: AsyncSession, set_id: uuid.UUID) -> None:
    """커밋한 세트를 지우고 커밋한다. 외래 키 순서대로 빈칸 → 세트 → 문서 → 사용자."""
    blank_set = await session.get(BlankSetRow, set_id)
    assert blank_set is not None
    doc_id, user_id = blank_set.document_id, blank_set.requested_by
    await session.execute(delete(BlankRow).where(BlankRow.set_id == set_id))
    await session.execute(delete(BlankSetRow).where(BlankSetRow.requested_by == user_id))
    await session.execute(delete(DocumentRow).where(DocumentRow.id == doc_id))
    await session.execute(delete(UserRow).where(UserRow.id == user_id))
    await session.commit()


@pytest.fixture
def wait_until_lock_wait() -> Callable[[async_sessionmaker[AsyncSession], int], Awaitable[None]]:
    return _wait_until_lock_wait


@pytest.fixture
def insert_user() -> Callable[[AsyncSession], Awaitable[uuid.UUID]]:
    return _insert_user


@pytest.fixture
def insert_blank_set() -> Callable[..., Awaitable[uuid.UUID]]:
    return _insert_blank_set


@pytest.fixture
def delete_blank_set() -> Callable[[AsyncSession, uuid.UUID], Awaitable[None]]:
    return _delete_blank_set
