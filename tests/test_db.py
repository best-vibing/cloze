"""DB 세팅 확인. DB가 필요한 테스트는 로컬에 DB가 없으면 건너뛰고, CI에서는 실패시킨다."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from cloze.config import load_settings
from cloze.infra.db import make_engine, make_sessionmaker

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"


def test_migrations_have_single_head() -> None:
    # 두 사람이 같은 부모에서 마이그레이션을 만들면 head가 둘이 된다. 머지 전에 CI에서 잡는다.
    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()
    assert len(heads) == 1, (
        f"마이그레이션 head가 {len(heads)}개: {heads}. `alembic merge heads`로 합친다"
    )


async def _select_one() -> int:
    engine = make_engine(load_settings())
    try:
        async with make_sessionmaker(engine)() as session:
            value: int = (await session.execute(text("SELECT 1"))).scalar_one()
            return value
    finally:
        await engine.dispose()


def test_session_reaches_db() -> None:
    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        result = asyncio.run(_select_one(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")
    assert result == 1
