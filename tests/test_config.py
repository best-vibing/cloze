from pathlib import Path

import pytest
from pydantic import ValidationError

from cloze.config import Settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 개발자 PC의 .env 와 CLOZE_ 환경 변수가 테스트 결과를 바꾸지 않게 한다.
    monkeypatch.chdir(tmp_path)
    for name in (
        "CLOZE_GEMINI_API_KEY",
        "CLOZE_GEMINI_MODEL",
        "CLOZE_SAMPLES_DIR",
        "CLOZE_DATABASE_URL",
        "CLOZE_TASK_TIMEOUT_S",
        "CLOZE_QUEUE_IDLE_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_env() -> None:
    settings = Settings()
    assert settings.gemini_api_key is None
    assert settings.samples_dir is None
    assert "@127.0.0.1:5432/" in settings.database_url.get_secret_value()


def test_database_url_hidden_when_printed() -> None:
    # 운영 주소에는 비밀번호가 들어 있다. 설정을 로그에 찍어도 드러나지 않아야 한다.
    assert "cloze:cloze" not in repr(Settings())


def test_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOZE_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("CLOZE_SAMPLES_DIR", "/tmp/samples")
    settings = Settings()
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "test-key"
    assert settings.samples_dir == Path("/tmp/samples")


def test_task_timeout_must_be_below_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # 같으면 처리 중인 작업이 다른 워커에 넘어가 두 번 실행될 수 있다(신뢰성 설계 2절).
    monkeypatch.setenv("CLOZE_TASK_TIMEOUT_S", "900")
    with pytest.raises(ValidationError, match="queue_idle_timeout_ms"):
        Settings()
