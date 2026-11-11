from pathlib import Path

import pytest
from pydantic import ValidationError

from cloze.config import Settings


def test_defaults_without_env() -> None:
    settings = Settings()
    assert settings.gemini_api_key is None
    assert settings.samples_dir is None


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
