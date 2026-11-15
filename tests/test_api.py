import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr

from cloze.api.app import create_app
from cloze.api.errors import STATUS, ApiError
from cloze.config import Settings

ORIGIN = "http://cloze.test"
CONTRACT = Path(__file__).parents[1] / "docs" / "design" / "data-contract.md"


class Body(BaseModel):
    n: int


def make_app(static_dir: Path) -> FastAPI:
    app = create_app(
        Settings(public_origin=ORIGIN, static_dir=static_dir, session_secret=SecretStr("t"))
    )

    @app.get("/boom")
    async def boom() -> None:
        raise ApiError("not_ready", "아직 처리 중이에요.")

    @app.post("/echo")
    async def echo(body: Body) -> Body:
        return body

    return app


def client(tmp_path: Path) -> TestClient:
    return TestClient(make_app(tmp_path / "no-frontend"))


def test_unknown_path_is_not_found_shape(tmp_path: Path) -> None:
    res = client(tmp_path).get("/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_api_error_uses_contract_status(tmp_path: Path) -> None:
    res = client(tmp_path).get("/boom")
    assert res.status_code == 409
    assert res.json() == {"error": {"code": "not_ready", "message": "아직 처리 중이에요."}}


def test_bad_body_is_invalid_request(tmp_path: Path) -> None:
    res = client(tmp_path).post("/echo", json={"n": "x"}, headers={"Origin": ORIGIN})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_request"


def test_origin_check(tmp_path: Path) -> None:
    c = client(tmp_path)
    for headers in ({"Origin": "http://evil.test"}, {}):
        res = c.post("/echo", json={"n": 1}, headers=headers)
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "forbidden_origin"
    assert c.post("/echo", json={"n": 1}, headers={"Origin": ORIGIN}).json() == {"n": 1}
    assert c.get("/boom").status_code == 409  # GET은 Origin을 보지 않는다


def test_serves_static_files_after_api_routes(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>cloze</h1>", encoding="utf-8")
    c = TestClient(make_app(tmp_path))
    assert "cloze" in c.get("/").text
    # create_app 안에서 등록한 라우트(/docs)가 "/" 마운트보다 먼저 잡힌다.
    # 테스트가 뒤에 붙인 /boom은 마운트에 가려지므로, 라우터는 create_app 안에 붙여야 한다.
    assert "swagger" in c.get("/docs").text.lower()
    assert c.get("/nope").json()["error"]["code"] == "not_found"


def test_status_table_matches_contract() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    section = text.split("## 3. 오류 형식과 코드")[1].split("\n## ")[0]
    rows = re.findall(r"^\| `(\w+)` \| (\d{3}) \|", section, re.MULTILINE)
    assert {code: int(status) for code, status in rows} == STATUS
