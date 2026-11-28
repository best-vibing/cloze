import asyncio
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import httpx
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from cloze.api.app import create_app
from cloze.api.auth import COOKIE, LoginLimiter, current_user, sign, unsign
from cloze.config import Settings, load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.users import UserRow, create_user, set_password

ORIGIN = "http://testserver"
SECRET = "test-secret-" + "x" * 32


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        public_origin=ORIGIN,
        static_dir=tmp_path / "no-frontend",
        session_secret=SecretStr(SECRET),
        image_signing_key=SecretStr("image-" + "y" * 32),
        cookie_secure=False,  # TestClient는 http라 Secure 쿠키를 돌려보내지 않는다
    )


def _db(work: str, login_id: str, password: str = "pw") -> None:
    async def run() -> None:
        engine = make_engine(load_settings())
        try:
            async with make_sessionmaker(engine)() as session:
                if work == "create":
                    await create_user(session, login_id, password, "사용자")
                else:
                    await set_password(session, login_id, password)
                await session.commit()
        finally:
            await engine.dispose()

    try:
        asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


@pytest.fixture
def login_id() -> str:
    login_id = f"test-{uuid.uuid4().hex[:8]}"
    _db("create", login_id)
    return login_id


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path))

    @app.get("/whoami")
    async def whoami(user: Annotated[UserRow, Depends(current_user)]) -> str:
        return user.login_id

    # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
    options = {"loop_factory": asyncio.SelectorEventLoop}
    with TestClient(app, headers={"Origin": ORIGIN}, backend_options=options) as c:
        yield c


def _use(c: TestClient, token: str) -> None:
    # 서버가 준 쿠키(도메인 testserver.local)와 따로 쌓이지 않게 비우고 넣는다.
    c.cookies.clear()
    c.cookies.set(COOKIE, token)


def _login(c: TestClient, login_id: str, password: str = "pw") -> int:
    return c.post("/auth/login", json={"login_id": login_id, "password": password}).status_code


def test_sign_round_trip_and_tamper() -> None:
    token = sign({"uid": "u", "ver": 0, "exp": 1}, b"k")
    assert unsign(token, b"k") == {"uid": "u", "ver": 0, "exp": 1}
    assert unsign(token, b"other") is None
    assert unsign(token[:-1] + ("0" if token[-1] != "0" else "1"), b"k") is None
    assert unsign("abc.éé", b"k") is None  # 비ASCII 서명도 500이 아니라 거절


def test_app_needs_long_session_secret(tmp_path: Path) -> None:
    for secret in (None, SecretStr(""), SecretStr("dev-only-change-me")):
        with pytest.raises(RuntimeError):
            create_app(
                Settings(
                    static_dir=tmp_path,
                    session_secret=secret,
                    image_signing_key=SecretStr("image-" + "y" * 32),
                )
            )


def test_too_long_login_fields_are_rejected(client: TestClient) -> None:
    too_long = [{"login_id": "a" * 65, "password": "pw"}, {"login_id": "a", "password": "p" * 257}]
    for body in too_long:
        res = client.post("/auth/login", json=body)
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "invalid_request"


def test_login_sets_session_cookie(client: TestClient, login_id: str) -> None:
    res = client.post("/auth/login", json={"login_id": login_id, "password": "pw"})
    assert res.status_code == 204
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=604800" in cookie
    assert client.get("/whoami").json() == login_id


def test_wrong_password_and_unknown_id_look_the_same(client: TestClient, login_id: str) -> None:
    wrong = client.post("/auth/login", json={"login_id": login_id, "password": "x"})
    unknown = client.post("/auth/login", json={"login_id": "nobody-" + login_id, "password": "x"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_session_is_checked_every_request(client: TestClient, login_id: str) -> None:
    assert client.get("/whoami").json()["error"]["code"] == "unauthorized"  # 쿠키 없음

    assert _login(client, login_id) == 204
    token = client.cookies[COOKIE]
    _use(client, token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert client.get("/whoami").status_code == 401  # 서명 위조

    _use(client, token)
    payload = unsign(token, SECRET.encode())
    assert payload is not None
    _use(client, sign({**payload, "exp": time.time() - 1}, SECRET.encode()))
    assert client.get("/whoami").status_code == 401  # 만료

    _use(client, token)
    assert client.get("/whoami").status_code == 200
    _db("set_password", login_id, "new")  # session_version이 올라간다
    assert client.get("/whoami").status_code == 401  # 기존 세션이 끊김


def test_too_many_failures_block_even_correct_password(client: TestClient, login_id: str) -> None:
    for _ in range(5):
        assert _login(client, login_id, "x") == 401
    res = client.post("/auth/login", json={"login_id": login_id, "password": "pw"})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "too_many_login_attempts"


def test_concurrent_failures_stop_at_limit(tmp_path: Path, login_id: str) -> None:
    # 확인(blocked)과 기록(fail) 사이에 await가 있으면 동시에 온 요청이 모두 한도를 통과한다
    # (#125 리뷰: 동시 20번이 모두 비밀번호 확인까지 갔다).
    app = create_app(_settings(tmp_path))
    body = {"login_id": login_id, "password": "x"}

    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN})
        async with app.router.lifespan_context(app), client:
            responses = await asyncio.gather(
                *(client.post("/auth/login", json=body) for _ in range(20))
            )
        return [res.status_code for res in responses]

    codes = asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    assert codes.count(401) == 5
    assert codes.count(429) == 15


def test_logout(client: TestClient, login_id: str) -> None:
    assert client.post("/auth/logout").status_code == 401  # 로그인 안 함
    assert _login(client, login_id) == 204
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/whoami").status_code == 401


def test_limiter_window_and_reset() -> None:
    limiter = LoginLimiter(limit=2, window_s=60)
    limiter.fail("a", 0)
    limiter.fail("a", 1)
    assert limiter.blocked("a", 2)
    assert not limiter.blocked("a", 62)  # 창이 지나면 풀림
    limiter.fail("b", 0)
    limiter.reset("b")
    assert not limiter.blocked("b", 0)


def test_limiter_keeps_at_most_max_keys() -> None:
    limiter = LoginLimiter(limit=5, window_s=60, max_keys=3)
    for i in range(10):
        limiter.fail(f"id{i}", 0.0)
    assert len(limiter._fails) == 3
    assert not limiter.blocked("id0", 1.0)  # 가장 먼저 들어온 기록부터 버렸다
