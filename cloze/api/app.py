"""FastAPI 앱 조립. 실행: uv run uvicorn --factory cloze.api.app:create_app --reload

import만으로 설정을 읽지 않도록 모듈 수준 app을 두지 않는다.
필수 설정이 없는 CI에서도 테스트가 이 모듈을 import할 수 있어야 한다.

API 라우터는 정적 파일 마운트보다 먼저 등록한다. 마운트가 "/" 아래를 모두 가져가기 때문이다.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from cloze.api import errors
from cloze.config import Settings, load_settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="cloze")
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

    @app.get("/health")
    async def health() -> dict[str, str]:
        # 프로세스가 요청을 받는지만 본다(DB·Redis는 보지 않는다). compose 상태 확인과
        # Tailscale Serve·Funnel 접속 확인(#83)에 쓴다.
        return {"status": "ok"}

    # 라우터는 여기에 추가한다(#34 로그인, #50 업로드 등).

    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
    return app
