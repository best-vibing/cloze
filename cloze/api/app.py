"""FastAPI 앱 조립. 실행: uv run uvicorn --factory cloze.api.app:create_app --reload

import만으로 설정을 읽지 않도록 모듈 수준 app을 두지 않는다.
필수 설정이 없는 CI에서도 테스트가 이 모듈을 import할 수 있어야 한다.

API 라우터는 정적 파일 마운트보다 먼저 등록한다. 마운트가 "/" 아래를 모두 가져가기 때문이다.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from cloze.api import auth, blank_sets, errors
from cloze.config import Settings, load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.storage import LocalStorage

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    # 짧거나 빈 키, 문서에 적힌 예시 값으로는 누구나 세션 쿠키를 위조할 수 있다.
    if settings.session_secret is None or len(settings.session_secret.get_secret_value()) < 32:
        raise RuntimeError("CLOZE_SESSION_SECRET이 없거나 32자보다 짧다. .env.example을 본다.")
    # 이미지 주소 서명 키도 같은 이유로 확인한다. 세션 키와 같으면 하나가 새면 둘 다 위조된다.
    image_key = settings.image_signing_key
    if image_key is None or len(image_key.get_secret_value()) < 32:
        raise RuntimeError("CLOZE_IMAGE_SIGNING_KEY가 없거나 32자보다 짧다. .env.example을 본다.")
    if image_key.get_secret_value() == settings.session_secret.get_secret_value():
        raise RuntimeError("CLOZE_IMAGE_SIGNING_KEY는 CLOZE_SESSION_SECRET과 달라야 한다.")
    engine = make_engine(settings)  # 연결은 첫 요청 때 맺는다

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="cloze", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_secret = settings.session_secret.get_secret_value().encode()
    app.state.image_signing_key = image_key.get_secret_value().encode()
    app.state.storage = LocalStorage(settings.storage_dir)
    app.state.sessions = make_sessionmaker(engine)
    app.state.login_limiter = auth.LoginLimiter(
        settings.login_fail_limit, settings.login_fail_window_s
    )
    errors.install(app)
    origin = settings.public_origin.rstrip("/")

    @app.middleware("http")
    async def check_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 상태를 바꾸는 요청은 자기 주소에서 온 것만 받는다.
        # Origin이 없어도 거절한다(데이터 계약 2절).
        if request.method in UNSAFE_METHODS and request.headers.get("origin") != origin:
            return errors.error_response("forbidden_origin", "다른 곳에서 보낸 요청은 받지 않아요.")
        return await call_next(request)

    # 라우터는 여기에 추가한다(마운트보다 앞).
    app.include_router(auth.router)
    app.include_router(blank_sets.router)

    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
    return app
