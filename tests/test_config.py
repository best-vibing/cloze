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
        "CLOZE_GEMINI_TIMEOUT_S",
        "CLOZE_GEMINI_MAX_CONCURRENCY",
        "CLOZE_SAMPLES_DIR",
        "CLOZE_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_env() -> None:
    settings = Settings()
    assert settings.gemini_api_key is None
    assert settings.samples_dir is None
    assert "@localhost:5432/" in settings.database_url.get_secret_value()


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


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid"])
def test_invalid_concurrency_environment_is_rejected(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """양의 정수 이외 환경 설정은 기존 Pydantic 규칙으로 거절된다."""
    monkeypatch.setenv("CLOZE_GEMINI_MAX_CONCURRENCY", value)
    with pytest.raises(ValidationError) as captured:
        Settings()
    assert captured.value.errors()[0]["loc"] == ("gemini_max_concurrency",)
