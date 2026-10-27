from pathlib import Path

import pytest

from cloze.config import Settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 개발자 PC의 .env 와 CLOZE_ 환경 변수가 테스트 결과를 바꾸지 않게 한다.
    monkeypatch.chdir(tmp_path)
    for name in ("CLOZE_GEMINI_API_KEY", "CLOZE_GEMINI_MODEL", "CLOZE_SAMPLES_DIR"):
        monkeypatch.delenv(name, raising=False)


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
