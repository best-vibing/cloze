import asyncio
import os
import uuid
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from cloze.api.app import create_app
from cloze.config import Settings, load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.blank_sets import BlankSetRow
from cloze.infra.db.users import create_user

ORIGIN = "http://testserver"
FIXTURES = Path(__file__).parent / "fixtures"
PDF = (FIXTURES / "sample.pdf").read_bytes()
OTHER_PDF = (FIXTURES / "twoup.pdf").read_bytes()


def _in_loop[T](coro: Coroutine[Any, Any, T]) -> T:
    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


async def _make_user(login_id: str) -> uuid.UUID:
    engine = make_engine(load_settings())
    try:
        async with make_sessionmaker(engine)() as session:
            user = await create_user(session, login_id, "pw", "사용자")
            await session.commit()
            return user.id
    finally:
        await engine.dispose()


async def _sets(user_id: uuid.UUID) -> list[BlankSetRow]:
    engine = make_engine(load_settings())
    try:
        async with make_sessionmaker(engine)() as session:
            rows = await session.scalars(
                select(BlankSetRow).where(BlankSetRow.requested_by == user_id)
            )
            return list(rows)
    finally:
        await engine.dispose()


class Uploader:
    def __init__(self, client: TestClient, user_id: uuid.UUID, queued: list[uuid.UUID]) -> None:
        self.client = client
        self.user_id = user_id
        self.queued = queued

    def upload(self, pdf: bytes = PDF, key: str | None = None, preset: str = "auto") -> Response:
        headers = {} if key is None else {"Idempotency-Key": key}
        return self.client.post(
            "/documents",
            files={"file": ("강의.pdf", pdf, "application/pdf")},
            data={"preset": preset},
            headers=headers,
        )


@pytest.fixture
def uploader(tmp_path: Path) -> Iterator[Uploader]:
    login_id = f"test-{uuid.uuid4().hex[:8]}"
    user_id = _in_loop(_make_user(login_id))
    queued: list[uuid.UUID] = []

    async def enqueue(set_id: uuid.UUID) -> None:
        queued.append(set_id)

    settings = Settings(
        public_origin=ORIGIN,
        static_dir=tmp_path / "no-frontend",
        storage_dir=tmp_path / "storage",
        session_secret=SecretStr("t"),
        cookie_secure=False,
    )
    app = create_app(settings, enqueue=enqueue)
    options = {"loop_factory": asyncio.SelectorEventLoop}
    with TestClient(app, headers={"Origin": ORIGIN}, backend_options=options) as client:
        login = client.post("/auth/login", json={"login_id": login_id, "password": "pw"})
        assert login.status_code == 204
        yield Uploader(client, user_id, queued)


def _key() -> str:
    return str(uuid.uuid4())


def test_upload_queues_a_set(uploader: Uploader, tmp_path: Path) -> None:
    res = uploader.upload(key=_key())
    assert res.status_code == 202
    body = res.json()
    assert uploader.queued == [uuid.UUID(body["blank_set_id"])]
    (row,) = _in_loop(_sets(uploader.user_id))
    assert row.status == "queued" and row.progress_done == 0 and row.progress_total >= 1
    assert row.preset_requested == "auto" and row.run_count == 0
    stored = tmp_path / "storage" / "documents" / body["document_id"] / "source.pdf"
    assert stored.read_bytes() == PDF


def test_same_key_returns_same_set_without_requeue(uploader: Uploader) -> None:
    key = _key()
    first = uploader.upload(key=key).json()
    again = uploader.upload(key=key)
    assert again.status_code == 202 and again.json() == first
    assert len(uploader.queued) == 1
    other = uploader.upload(key=key, preset="vocab")  # 같은 키에 다른 내용
    assert other.status_code == 422 and other.json()["error"]["code"] == "invalid_request"


def test_same_pdf_new_key_reuses_document(uploader: Uploader) -> None:
    first = uploader.upload(key=_key()).json()
    second = uploader.upload(key=_key()).json()
    third = uploader.upload(pdf=OTHER_PDF, key=_key()).json()
    assert second["document_id"] == first["document_id"]
    assert second["blank_set_id"] != first["blank_set_id"]
    assert third["document_id"] != first["document_id"]
    assert len(uploader.queued) == 3


@pytest.mark.parametrize(
    ("pdf", "key", "preset", "status", "code"),
    [
        (PDF, None, "auto", 422, "invalid_request"),  # 키 없음
        (PDF, "not-a-uuid", "auto", 422, "invalid_request"),
        (PDF, "KEY", "nope", 422, "invalid_request"),  # 없는 방식
        (b"PK\x03\x04 zip", "KEY", "auto", 415, "not_pdf"),
    ],
)
def test_rejections_create_nothing(
    uploader: Uploader, pdf: bytes, key: str | None, preset: str, status: int, code: str
) -> None:
    res = uploader.upload(pdf=pdf, key=_key() if key == "KEY" else key, preset=preset)
    assert (res.status_code, res.json()["error"]["code"]) == (status, code)
    assert uploader.queued == [] and _in_loop(_sets(uploader.user_id)) == []


def test_requires_login(tmp_path: Path) -> None:
    async def enqueue(_: uuid.UUID) -> None:
        pass

    settings = Settings(static_dir=tmp_path, session_secret=SecretStr("t"), public_origin=ORIGIN)
    client = TestClient(create_app(settings, enqueue=enqueue), headers={"Origin": ORIGIN})
    res = client.post(
        "/documents", files={"file": ("a.pdf", PDF)}, headers={"Idempotency-Key": _key()}
    )
    assert res.status_code == 401
