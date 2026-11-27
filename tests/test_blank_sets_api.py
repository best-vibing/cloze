"""GET /blank-sets/{id}와 GET /images/{key}(#53). 워커 없이 DB 행을 직접 넣는다.

로컬에 DB가 없으면 DB를 쓰는 테스트는 건너뛰고, CI에서는 실패시킨다(test_db와 같다).
API가 자기 세션으로 읽으므로 행을 커밋하고, 테스트가 끝나면 지운다.
"""

import asyncio
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from cloze.api.app import create_app
from cloze.api.auth import COOKIE, sign
from cloze.config import Settings, load_settings
from cloze.infra import signed_url
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.blank_sets import BlankSetRow
from cloze.infra.db.blanks import BlankRow
from cloze.infra.db.documents import DocumentRow
from cloze.infra.db.pages import BlankSetPageRow, PageRow
from cloze.infra.db.users import UserRow
from cloze.infra.storage import LocalStorage, redacted_key, source_key

ORIGIN = "http://testserver"
SESSION_SECRET = "session-" + "s" * 32
IMAGE_KEY = "image-" + "i" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 fake"
SECRET_FIELDS = {"answer", "variants", "answer_group", "token_ids"}
BLANK_FIELDS = {"blank_id", "order", "bbox", "type", "hint", "style"}
TOP_FIELDS = {
    "blank_set_id",
    "document_id",
    "status",
    "progress",
    "preset_requested",
    "preset_applied",
    "preset_reason",
    "pages",
}


def _run[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def main() -> T:
        engine = make_engine(load_settings())
        try:
            async with make_sessionmaker(engine)() as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    try:
        # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
        return asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("로컬 DB가 없다: docker compose up -d postgres")


def _answer(page_no: int, order: int) -> str:
    return f"정답-{page_no}-{order}-미토콘드리아"


@dataclass
class Made:
    set_id: uuid.UUID
    document_id: uuid.UUID
    redacted: dict[int, str]  # 쪽 번호 → 가린 이미지 키
    original: dict[int, str]  # 쪽 번호 → 원본 쪽 이미지 키


@dataclass
class Seed:
    """테스트용 행을 넣고, 끝나면 넣은 것만 지운다."""

    storage: LocalStorage
    users: list[uuid.UUID] = field(default_factory=list)

    def user(self) -> uuid.UUID:
        async def work(session: AsyncSession) -> uuid.UUID:
            row = UserRow(login_id=f"t-{uuid.uuid4().hex[:8]}", password_hash="x", display_name="t")
            session.add(row)
            await session.flush()
            return row.id

        user_id = _run(work)
        self.users.append(user_id)
        return user_id

    def blank_set(
        self,
        owner: uuid.UUID,
        status: str = "done",
        *,
        total: int = 2,
        failed_pages: tuple[int, ...] = (),
        blanks_per_page: int = 2,
        deleted: bool = False,
        **fields: Any,
    ) -> Made:
        done_pages = [n for n in range(1, total + 1) if n not in failed_pages]
        finished = status in ("done", "partial")

        async def work(session: AsyncSession) -> Made:
            doc = DocumentRow(
                owner_id=owner,
                sha256=uuid.uuid4().hex,
                filename="t.pdf",
                page_count=total,
                size_bytes=1,
                source_key="",
                deleted_at=datetime.now(UTC) if deleted else None,
            )
            session.add(doc)
            await session.flush()
            doc.source_key = source_key(doc.id)
            values: dict[str, Any] = {
                "preset_applied": "concept",
                "analysis": {"doc_type": "강의", "preset": "concept", "reason": "슬라이드라서"},
                "extract_version": 1,
                "progress_done": total if status in ("done", "partial", "failed") else 0,
            }
            values.update(fields)
            blank_set = BlankSetRow(
                document_id=doc.id,
                requested_by=owner,
                idempotency_key=uuid.uuid4().hex,
                request_hash="h",
                preset_requested="auto",
                seed=1,
                status=status,
                progress_total=total,
                **values,
            )
            session.add(blank_set)
            await session.flush()
            made = Made(blank_set.id, doc.id, {}, {})
            for n in range(1, total + 1):
                made.original[n] = f"documents/{doc.id}/pages/{n}.png"
                session.add(
                    PageRow(
                        document_id=doc.id,
                        page_no=n,
                        extract_version=1,
                        width_pt=720,
                        height_pt=540,
                        source="text",
                        image_key=made.original[n],
                        tokens_key=f"documents/{doc.id}/pages/{n}.json",
                    )
                )
            await session.flush()
            for n in done_pages if finished else []:
                made.redacted[n] = redacted_key(doc.id, blank_set.id, n)
                session.add(
                    BlankSetPageRow(
                        set_id=blank_set.id,
                        page_no=n,
                        image_key=made.redacted[n],
                        selection_source="rule",
                    )
                )
                for order in range(1, blanks_per_page + 1):
                    session.add(
                        BlankRow(
                            set_id=blank_set.id,
                            page_no=n,
                            order=order,
                            token_ids=[order],
                            bbox=[0.1, 0.2, 0.3, 0.25],
                            type="term",
                            hint="세포 소기관" if order == 1 else None,
                            style={
                                "font_size_ratio": 0.025,
                                "font_color": "#1a1a1a",
                                "bg_color": "#ffffff",
                            },
                            answer=_answer(n, order),
                            variants=[_answer(n, order) + "-다른표기"],
                            answer_group=7,
                        )
                    )
            return made

        made = _run(work)
        for key in made.redacted.values():
            self.storage.put(key, WEBP)
        for key in made.original.values():
            self.storage.put(key, b"\x89PNG original")
        self.storage.put(source_key(made.document_id), b"%PDF-1.7 original")
        return made

    def cleanup(self) -> None:
        if not self.users:
            return

        async def work(session: AsyncSession) -> None:
            sets = BlankSetRow.requested_by.in_(self.users)
            set_ids = BlankSetRow.__table__.select().with_only_columns(BlankSetRow.id).where(sets)
            docs = DocumentRow.__table__.select().with_only_columns(DocumentRow.id)
            docs = docs.where(DocumentRow.owner_id.in_(self.users))
            await session.execute(delete(BlankRow).where(BlankRow.set_id.in_(set_ids)))
            await session.execute(
                delete(BlankSetPageRow).where(BlankSetPageRow.set_id.in_(set_ids))
            )
            await session.execute(delete(BlankSetRow).where(sets))
            await session.execute(delete(PageRow).where(PageRow.document_id.in_(docs)))
            await session.execute(delete(DocumentRow).where(DocumentRow.owner_id.in_(self.users)))
            await session.execute(delete(UserRow).where(UserRow.id.in_(self.users)))

        _run(work)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        public_origin=ORIGIN,
        static_dir=tmp_path / "no-frontend",
        session_secret=SecretStr(SESSION_SECRET),
        image_signing_key=SecretStr(IMAGE_KEY),
        cookie_secure=False,
        storage_dir=tmp_path / "storage",
    )


@pytest.fixture
def seed(tmp_path: Path) -> Iterator[Seed]:
    s = Seed(LocalStorage(tmp_path / "storage"))
    yield s
    s.cleanup()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path))
    options = {"loop_factory": asyncio.SelectorEventLoop}
    with TestClient(app, headers={"Origin": ORIGIN}, backend_options=options) as c:
        yield c


def _as(c: TestClient, user_id: uuid.UUID) -> TestClient:
    token = sign(
        {"uid": str(user_id), "ver": 0, "exp": time.time() + 3600}, SESSION_SECRET.encode()
    )
    c.cookies.clear()
    c.cookies.set(COOKIE, token)
    return c


def _keys(value: Any) -> set[str]:
    """JSON 안의 모든 키(재귀)."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _signed(key: str, now: float | None = None) -> str:
    return signed_url.sign(key, IMAGE_KEY.encode(), time.time() if now is None else now)


# --- 서명 URL(DB 없음) ---


def test_signed_url_round_trip_and_tamper() -> None:
    secret, now = b"k" * 32, 1_000_000.0
    url = signed_url.sign("documents/a/blank_sets/b/1.webp", secret, now)
    parts = urlsplit(url)
    assert parts.path == "/images/documents/a/blank_sets/b/1.webp"
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    exp, sig = int(query["exp"]), query["sig"]
    assert exp == now + 15 * 60
    key = "documents/a/blank_sets/b/1.webp"
    assert signed_url.verify(key, exp, sig, secret, now)
    assert signed_url.verify(key, exp, sig, secret, exp - 1)
    assert not signed_url.verify(key, exp, sig, secret, exp)  # 만료 시각부터 거절
    assert not signed_url.verify(key, exp + 60, sig, secret, now)  # exp 변조
    assert not signed_url.verify("documents/a/blank_sets/b/2.webp", exp, sig, secret, now)
    assert not signed_url.verify(key, exp, sig, b"other" * 8, now)  # 다른 키로 위조
    assert not signed_url.verify(key, exp, "0" * 64, secret, now)
    assert not signed_url.verify(key, exp, "é", secret, now)  # 비ASCII도 예외 없이 거절


def test_app_needs_separate_long_image_key(tmp_path: Path) -> None:
    for key in (None, SecretStr(""), SecretStr("short"), SecretStr(SESSION_SECRET)):
        settings = _settings(tmp_path).model_copy(update={"image_signing_key": key})
        with pytest.raises(RuntimeError):
            create_app(settings)


# --- GET /blank-sets/{id} ---


def test_needs_login(client: TestClient) -> None:
    res = client.get(f"/blank-sets/{uuid.uuid4()}")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_done_set_shape(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "done", total=2)
    res = _as(client, owner).get(f"/blank-sets/{made.set_id}")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == TOP_FIELDS  # failed가 아니면 error 키가 없다
    assert body["blank_set_id"] == str(made.set_id)
    assert body["document_id"] == str(made.document_id)
    assert body["status"] == "done"
    assert body["progress"] == {"done": 2, "total": 2}
    assert (body["preset_requested"], body["preset_applied"]) == ("auto", "concept")
    assert body["preset_reason"] == "슬라이드라서"
    assert [p["page_no"] for p in body["pages"]] == [1, 2]
    page = body["pages"][0]
    assert set(page) == {"page_no", "image_url", "width_pt", "height_pt", "blanks"}
    assert (page["width_pt"], page["height_pt"]) == (720, 540)
    assert page["image_url"].startswith(f"/images/{made.redacted[1]}?exp=")
    assert [b["order"] for b in page["blanks"]] == [1, 2]
    blank = page["blanks"][0]
    assert set(blank) == BLANK_FIELDS
    assert blank["bbox"] == [0.1, 0.2, 0.3, 0.25]
    assert (blank["type"], blank["hint"]) == ("term", "세포 소기관")
    assert blank["style"] == {
        "font_size_ratio": 0.025,
        "font_color": "#1a1a1a",
        "bg_color": "#ffffff",
    }

    image = client.get(page["image_url"])
    assert image.status_code == 200
    assert image.content == WEBP
    assert image.headers["content-type"] == "image/webp"
    cache = image.headers["cache-control"]
    assert cache.startswith("private, max-age=")
    assert 0 < int(cache.rsplit("=", 1)[1]) <= 15 * 60


def test_response_never_has_answers(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    for status in ("done", "partial"):
        made = seed.blank_set(
            owner, status, total=3, failed_pages=(2,) if status == "partial" else ()
        )
        res = _as(client, owner).get(f"/blank-sets/{made.set_id}")
        assert res.status_code == 200
        assert not _keys(res.json()) & SECRET_FIELDS
        # 키 이름이 바뀌어도 값이 새면 잡히게, 정답 문자열 자체도 찾는다.
        assert "미토콘드리아" not in res.text
        assert sum(len(p["blanks"]) for p in res.json()["pages"]) > 0


@pytest.mark.parametrize("status", ["queued", "running"])
def test_in_progress_has_no_pages(client: TestClient, seed: Seed, status: str) -> None:
    owner = seed.user()
    applied = None if status == "queued" else "concept"
    analysis = None if status == "queued" else {"reason": "슬라이드라서"}
    made = seed.blank_set(
        owner, status, total=4, preset_applied=applied, analysis=analysis, progress_done=1
    )
    body = _as(client, owner).get(f"/blank-sets/{made.set_id}").json()
    assert set(body) == TOP_FIELDS
    assert body["status"] == status
    assert body["pages"] == []
    assert body["progress"] == {"done": 1, "total": 4}
    assert body["preset_applied"] == applied
    assert body["preset_reason"] == (None if status == "queued" else "슬라이드라서")


def test_partial_drops_failed_pages(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "partial", total=3, failed_pages=(2,))
    body = _as(client, owner).get(f"/blank-sets/{made.set_id}").json()
    assert body["status"] == "partial"
    assert body["progress"] == {"done": 3, "total": 3}
    assert [p["page_no"] for p in body["pages"]] == [1, 3]


def test_page_without_blanks_still_listed(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "partial", total=1, blanks_per_page=0)
    body = _as(client, owner).get(f"/blank-sets/{made.set_id}").json()
    assert [(p["page_no"], p["blanks"]) for p in body["pages"]] == [(1, [])]


def test_failed_has_error(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(
        owner,
        "failed",
        total=12,
        preset_applied=None,
        analysis=None,
        error_code="unreadable_pdf",
        error_message="인식이 잘 안 됐어요, 다시 올려 주세요.",
    )
    body = _as(client, owner).get(f"/blank-sets/{made.set_id}").json()
    assert set(body) == TOP_FIELDS | {"error"}
    assert body["pages"] == []
    assert body["progress"] == {"done": 12, "total": 12}
    assert (body["preset_applied"], body["preset_reason"]) == (None, None)
    assert body["error"] == {
        "code": "unreadable_pdf",
        "message": "인식이 잘 안 됐어요, 다시 올려 주세요.",
    }


def test_failed_without_message_uses_default(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "failed", error_code=None, error_message=None)
    error = _as(client, owner).get(f"/blank-sets/{made.set_id}").json()["error"]
    assert error["code"] == "processing_failed"
    assert error["message"]


def test_hidden_sets_look_like_missing(client: TestClient, seed: Seed) -> None:
    owner, other = seed.user(), seed.user()
    others = seed.blank_set(other, "done")
    deleted = seed.blank_set(owner, "done", deleted=True)
    _as(client, owner)
    responses = [
        client.get(f"/blank-sets/{uuid.uuid4()}"),  # 없는 세트
        client.get("/blank-sets/not-a-uuid"),  # 모양이 틀린 ID
        client.get(f"/blank-sets/{others.set_id}"),  # 남의 세트
        client.get(f"/blank-sets/{deleted.set_id}"),  # 삭제 표시한 문서의 세트
    ]
    assert {r.status_code for r in responses} == {404}
    assert len({r.text for r in responses}) == 1
    assert responses[0].json()["error"]["code"] == "not_found"


def test_poll_uses_few_queries(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "done", total=5, blanks_per_page=4)
    statements: list[str] = []

    def count(*args: Any) -> None:
        statements.append(args[2])

    event.listen(Engine, "before_cursor_execute", count)
    try:
        assert _as(client, owner).get(f"/blank-sets/{made.set_id}").status_code == 200
    finally:
        event.remove(Engine, "before_cursor_execute", count)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    # 사용자 확인 1, 세트 1, 쪽·빈칸 1. 쪽 수가 늘어도 그대로다.
    assert len(selects) == 3, selects


# --- GET /images/{key} ---


def test_bad_image_urls_are_rejected(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "done", total=2)
    key = made.redacted[1]
    good = urlsplit(_signed(key))
    query = {k: v[0] for k, v in parse_qs(good.query).items()}
    exp, sig = int(query["exp"]), query["sig"]
    expired = _signed(key, now=time.time() - 16 * 60)
    _as(client, owner)
    assert client.get(f"{good.path}?{good.query}").status_code == 200
    bad = [
        client.get(expired),
        client.get(f"/images/{key}?exp={exp}&sig={'0' * 64}"),  # 서명 위조
        client.get(f"/images/{key}?exp={exp + 3600}&sig={sig}"),  # exp 변조
        client.get(f"/images/{made.redacted[2]}?exp={exp}&sig={sig}"),  # key 변조
        client.get(f"/images/{key}"),  # 서명 없음
        client.get(f"/images/{key}?exp=soon&sig={sig}"),
        client.get(f"/images/{key}?exp={exp}&sig=%C3%A9"),
    ]
    assert {r.status_code for r in bad} == {404}
    assert {r.json()["error"]["code"] for r in bad} == {"not_found"}


def test_image_needs_owner_and_live_document(client: TestClient, seed: Seed) -> None:
    owner, other = seed.user(), seed.user()
    mine = seed.blank_set(owner, "done")
    deleted = seed.blank_set(owner, "done", deleted=True)
    _as(client, other)
    # 서명이 맞아도(주소를 넘겨받아도) 남의 이미지는 열리지 않는다.
    others = client.get(_signed(mine.redacted[1]))
    missing = client.get(_signed(f"documents/{uuid.uuid4()}/blank_sets/{uuid.uuid4()}/1.webp"))
    _as(client, owner)
    gone = client.get(_signed(deleted.redacted[1]))
    responses = [others, missing, gone]
    assert {r.status_code for r in responses} == {404}
    assert len({r.text for r in responses}) == 1
    assert client.get(_signed(mine.redacted[1])).status_code == 200
    client.cookies.clear()
    assert client.get(_signed(mine.redacted[1])).status_code == 401


def test_original_files_are_never_served(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "done")
    _as(client, owner)
    # 서버 키로 바르게 서명해도 원본 PDF·원본 쪽 이미지는 내보내지 않는다.
    for key in (source_key(made.document_id), made.original[1]):
        res = client.get(_signed(key))
        assert res.status_code == 404
        assert b"original" not in res.content


def test_missing_image_file_is_not_found(client: TestClient, seed: Seed) -> None:
    owner = seed.user()
    made = seed.blank_set(owner, "done")
    seed.storage.delete(made.redacted[1])
    res = _as(client, owner).get(_signed(made.redacted[1]))
    assert res.status_code == 404
