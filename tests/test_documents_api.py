"""POST /documents(#50). DB가 필요하다: 로컬에 없으면 건너뛰고 CI에서는 실패시킨다."""

import asyncio
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from cloze.api import documents
from cloze.api.app import create_app
from cloze.api.auth import COOKIE, sign
from cloze.config import Settings, load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.blank_sets import BlankSetRow
from cloze.infra.db.documents import DocumentRow
from cloze.infra.db.users import UserRow

ORIGIN = "http://testserver"
SECRET = "test-secret-" + "x" * 32
PDF = (Path(__file__).parent / "fixtures" / "sample.pdf").read_bytes()
OTHER_PDF = (Path(__file__).parent / "fixtures" / "korean.pdf").read_bytes()


def _db[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = make_engine(load_settings())
        try:
            async with make_sessionmaker(engine)() as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    try:
        return asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


async def _new_user(session: AsyncSession) -> uuid.UUID:
    # 비밀번호 확인을 거치지 않고 쿠키를 직접 서명하므로 해시는 아무 값이나 넣는다.
    user = UserRow(login_id=f"test-{uuid.uuid4().hex[:8]}", password_hash="x", display_name="t")
    session.add(user)
    await session.flush()
    return user.id


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []

    async def fake(set_id: uuid.UUID) -> None:
        calls.append(set_id)

    monkeypatch.setattr(documents, "enqueue", fake)
    return calls


def _client(tmp_path: Path, **overrides: Any) -> TestClient:
    settings = Settings(
        public_origin=ORIGIN,
        static_dir=tmp_path / "no-frontend",
        storage_dir=tmp_path / "storage",
        session_secret=SecretStr(SECRET),
        cookie_secure=False,
        **overrides,
    )
    options = {"loop_factory": asyncio.SelectorEventLoop}
    return TestClient(create_app(settings), headers={"Origin": ORIGIN}, backend_options=options)


@pytest.fixture
def client(tmp_path: Path, enqueued: list[uuid.UUID]) -> Iterator[TestClient]:
    user_id = _db(_new_user)
    with _client(tmp_path) as c:
        c.cookies.set(
            COOKIE, sign({"uid": str(user_id), "ver": 0, "exp": time.time() + 60}, SECRET.encode())
        )
        yield c


def _upload(c: TestClient, key: str | None = None, pdf: bytes = PDF, **form: str) -> Any:
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    return c.post(
        "/documents", files={"file": ("a.pdf", pdf, "application/pdf")}, data=form, headers=headers
    )


def _rows(set_id: str) -> tuple[BlankSetRow, DocumentRow]:
    async def get(session: AsyncSession) -> tuple[BlankSetRow, DocumentRow]:
        blank_set = await session.get(BlankSetRow, uuid.UUID(set_id))
        assert blank_set is not None
        document = await session.get(DocumentRow, blank_set.document_id)
        assert document is not None
        return blank_set, document

    return _db(get)


def test_upload_queues_set_and_enqueues_once(client: TestClient, enqueued: list[uuid.UUID]) -> None:
    res = _upload(client, preset="concept")
    assert res.status_code == 202
    body = res.json()
    blank_set, document = _rows(body["blank_set_id"])
    assert str(document.id) == body["document_id"]
    assert blank_set.status == "queued" and blank_set.preset_requested == "concept"
    assert blank_set.progress_total == document.page_count >= 1
    assert enqueued == [blank_set.id]


def test_same_key_returns_same_set_without_enqueue(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    key = str(uuid.uuid4())
    first = _upload(client, key)
    again = _upload(client, key)
    assert again.status_code == 202 and again.json() == first.json()
    assert len(enqueued) == 1

    for different in ({"preset": "vocab"}, {"pdf": OTHER_PDF}):
        res = _upload(client, key, **different)
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "invalid_request"


def test_same_pdf_reuses_document_with_new_set(client: TestClient) -> None:
    first = _upload(client).json()
    second = _upload(client).json()
    assert second["document_id"] == first["document_id"]
    assert second["blank_set_id"] != first["blank_set_id"]


def test_bad_requests_are_rejected(client: TestClient) -> None:
    no_key = client.post("/documents", files={"file": ("a.pdf", PDF, "application/pdf")})
    bad_key = _upload(client, "not-a-uuid")
    bad_preset = _upload(client, preset="everything")
    not_pdf = _upload(client, pdf=b"hello")
    for res, code in (
        (no_key, "invalid_request"),
        (bad_key, "invalid_request"),
        (bad_preset, "invalid_request"),
        (not_pdf, "not_pdf"),
    ):
        assert res.json()["error"]["code"] == code


def test_too_large_file_is_rejected(tmp_path: Path, enqueued: list[uuid.UUID]) -> None:
    user_id = _db(_new_user)
    with _client(tmp_path, upload_max_bytes=len(PDF) - 1) as c:
        c.cookies.set(
            COOKIE, sign({"uid": str(user_id), "ver": 0, "exp": time.time() + 60}, SECRET.encode())
        )
        res = _upload(c)
    assert res.status_code == 413 and res.json()["error"]["code"] == "file_too_large"
    assert enqueued == []


def test_upload_needs_login(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        assert _upload(c).json()["error"]["code"] == "unauthorized"


def test_enqueue_failure_still_returns_202(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_: uuid.UUID) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(documents, "enqueue", broken)
    res = _upload(client)
    assert res.status_code == 202
    blank_set, _ = _rows(res.json()["blank_set_id"])
    assert blank_set.status == "queued"
