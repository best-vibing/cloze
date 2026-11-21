"""API 오류 응답. 모양과 코드 목록은 데이터 계약 3절."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

# 계약 3절 표와 같아야 한다(tests/test_api.py가 확인한다).
STATUS: dict[str, int] = {
    "unauthorized": 401,
    "invalid_credentials": 401,
    "forbidden_origin": 403,
    "not_found": 404,
    "not_ready": 409,
    "file_too_large": 413,
    "not_pdf": 415,
    "invalid_request": 422,
    "encrypted_pdf": 422,
    "too_many_pages": 422,
    "daily_limit_exceeded": 429,
    "too_many_running": 429,
    "too_many_login_attempts": 429,
    "service_limit_exceeded": 503,
}


class ApiError(Exception):
    """라우터가 던지면 계약 모양의 오류 응답이 된다. 없는 코드는 KeyError로 바로 드러난다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = STATUS[code]


def error_response(code: str, message: str, status: int | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    return JSONResponse(body, status_code=status or STATUS[code])


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response("invalid_request", "요청 형식이 올바르지 않아요.")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        # 프레임워크가 내는 오류(없는 경로 404, 허용하지 않는 메서드 405 등)도 같은 모양으로 바꾼다.
        if exc.status_code == 404:
            return error_response("not_found", "찾을 수 없어요.")
        return error_response("invalid_request", "요청 형식이 올바르지 않아요.", exc.status_code)
