import asyncio
import getpass
import os
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from cloze.cli import accounts
from cloze.config import load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.users import create_user, get_user_by_login_id, set_password
from cloze.infra.passwords import hash_password, verify_password


def test_password_round_trip() -> None:
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong horse", stored)


def test_same_password_gets_different_salt() -> None:
    assert hash_password("same") != hash_password("same")


def _in_db[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = make_engine(load_settings())
        try:
            async with make_sessionmaker(engine)() as session:
                return await work(session)
        finally:
            await engine.dispose()

    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        return asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


def _login_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def test_login_id_is_unique() -> None:
    login_id = _login_id()

    async def work(session: AsyncSession) -> None:
        await create_user(session, login_id, "pw", "첫째")
        await session.commit()
        with pytest.raises(IntegrityError):
            await create_user(session, login_id, "pw", "둘째")

    _in_db(work)


def test_set_password_bumps_session_version() -> None:
    login_id = _login_id()

    async def work(session: AsyncSession) -> tuple[int, int, bool, bool]:
        user = await create_user(session, login_id, "old", "사용자")
        await session.commit()
        before = user.session_version
        assert await set_password(session, login_id, "new")
        await session.commit()
        after = await get_user_by_login_id(session, login_id)
        assert after is not None
        return (
            before,
            after.session_version,
            verify_password("new", after.password_hash),
            await set_password(session, _login_id(), "x"),
        )

    before, after, new_ok, missing = _in_db(work)
    assert (before, after, new_ok, missing) == (0, 1, True, False)


def test_accounts_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "pw")
    login_id = _login_id()
    codes = []
    for argv in (
        ["create-user", login_id, "--display-name", "사용자"],
        ["create-user", login_id, "--display-name", "사용자"],
        ["set-password", login_id],
        ["set-password", _login_id()],
    ):
        try:
            accounts.main(argv)
        except SystemExit as done:
            codes.append(done.code)
        except OperationalError:
            if os.environ.get("CI"):
                raise
            pytest.skip("로컬 DB가 없다: docker compose up -d postgres")
    assert codes == [0, 1, 0, 1]
    assert "이미 있는 아이디" in capsys.readouterr().out
