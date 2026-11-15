"""로그인·로그아웃과 세션 쿠키(데이터 계약 2절, 아키텍처 3.1).

쿠키 값은 `base64(JSON{uid, ver, exp}).HMAC-SHA256`이다. 서명만 믿지 않고 요청마다
사용자가 있는지, 쿠키의 ver가 users.session_version과 같은지 DB에서 확인한다.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections import defaultdict, deque
from functools import cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cloze.api.deps import db_session
from cloze.api.errors import ApiError
from cloze.config import Settings
from cloze.infra.db.users import UserRow, get_user, get_user_by_login_id
from cloze.infra.passwords import hash_password, verify_password

COOKIE = "cloze_session"
MAX_AGE_S = 7 * 24 * 3600  # 7일 [가설] (계약 2절)

router = APIRouter()


def sign(payload: dict[str, Any], secret: bytes) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    mac = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def unsign(token: str, secret: bytes) -> dict[str, Any] | None:
    body, _, mac = token.rpartition(".")
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if not body or not hmac.compare_digest(mac, expected):
        return None
    payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    return payload


class LoginLimiter:
    """아이디별 최근 실패 시각. 인스턴스가 하나라 프로세스 메모리로 충분하다(신뢰성 설계 5절)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._fails: defaultdict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str, now: float) -> bool:
        fails = self._fails[key]
        while fails and now - fails[0] > self.window_s:
            fails.popleft()
        if not fails:
            del self._fails[key]  # 없는 아이디를 계속 넣어도 메모리가 쌓이지 않게
            return False
        return len(fails) >= self.limit

    def fail(self, key: str, now: float) -> None:
        self._fails[key].append(now)

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


@cache
def _dummy_hash() -> str:
    # 없는 아이디에도 해시를 한 번 계산해, 응답 시간으로 아이디가 있는지 새지 않게 한다.
    return hash_password(secrets.token_hex(16))


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _secret(request: Request) -> bytes:
    # create_app이 설정을 확인하고 넣어 둔다(없으면 앱이 뜨지 않는다).
    secret: bytes = request.app.state.session_secret
    return secret


Db = Annotated[AsyncSession, Depends(db_session)]


async def current_user(request: Request, db: Db) -> UserRow:
    unauthorized = ApiError("unauthorized", "로그인이 필요해요.")
    token = request.cookies.get(COOKIE)
    payload = unsign(token, _secret(request)) if token else None
    if payload is None or payload["exp"] < time.time():
        raise unauthorized
    user = await get_user(db, uuid.UUID(payload["uid"]))
    if user is None or user.session_version != payload["ver"]:
        raise unauthorized
    return user


class LoginBody(BaseModel):
    login_id: str
    password: str


@router.post("/auth/login", status_code=204)
async def login(body: LoginBody, request: Request, response: Response, db: Db) -> None:
    settings = _settings(request)
    limiter: LoginLimiter = request.app.state.login_limiter
    if limiter.blocked(body.login_id, time.monotonic()):
        raise ApiError("too_many_login_attempts", "잠시 뒤에 다시 시도해 주세요.")
    user = await get_user_by_login_id(db, body.login_id)
    stored = user.password_hash if user else _dummy_hash()
    # scrypt는 CPU를 0.x초 쓴다. 이벤트 루프를 막지 않도록 스레드에서 돌린다.
    matched = await asyncio.to_thread(verify_password, body.password, stored)
    if user is None or not matched:
        limiter.fail(body.login_id, time.monotonic())
        raise ApiError("invalid_credentials", "아이디 또는 비밀번호가 맞지 않아요.")
    limiter.reset(body.login_id)
    payload = {"uid": str(user.id), "ver": user.session_version, "exp": time.time() + MAX_AGE_S}
    response.set_cookie(
        COOKIE,
        sign(payload, _secret(request)),
        max_age=MAX_AGE_S,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request, response: Response, _: Annotated[UserRow, Depends(current_user)]
) -> None:
    response.delete_cookie(
        COOKIE, httponly=True, secure=_settings(request).cookie_secure, samesite="lax"
    )
