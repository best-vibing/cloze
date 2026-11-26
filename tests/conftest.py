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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


@pytest.fixture
def wait_until_lock_wait() -> Callable[[async_sessionmaker[AsyncSession], int], Awaitable[None]]:
    return _wait_until_lock_wait


@pytest.fixture
def insert_user() -> Callable[[AsyncSession], Awaitable[uuid.UUID]]:
    return _insert_user
