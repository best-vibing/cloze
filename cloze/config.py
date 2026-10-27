"""환경 변수에서 설정을 읽는다. 모든 변수는 CLOZE_ 로 시작한다."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOZE_", env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_s: float = 60.0
    # 기준 PDF 세트 위치. 저작권 때문에 레포에 두지 않고, 없으면 해당 테스트를 건너뛴다.
    samples_dir: Path | None = None


def load_settings() -> Settings:
    return Settings()
