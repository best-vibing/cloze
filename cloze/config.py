"""환경 변수에서 설정을 읽는다. 모든 변수는 CLOZE_ 로 시작한다."""

from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOZE_", env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_s: float = 60.0
    # 기준 PDF 세트 위치. 저작권 때문에 레포에 두지 않고, 없으면 해당 테스트를 건너뛴다.
    samples_dir: Path | None = None
    # 기본값은 로컬 개발용(docker-compose.yml의 postgres). 운영 주소는 .env로 넣는다.
    database_url: SecretStr = SecretStr("postgresql+psycopg://cloze:cloze@localhost:5432/cloze")
    redis_url: str = "redis://localhost:6379/0"
    # 신뢰성 설계 2절. 작업 제한 시간이 idle_timeout 이상이면 살아서 처리 중인 작업이
    # 다른 워커로 넘어가 두 번 실행된다.
    task_timeout_s: int = 480
    queue_idle_timeout_ms: int = 900_000

    @model_validator(mode="after")
    def _timeout_below_idle(self) -> "Settings":
        if self.task_timeout_s * 1000 >= self.queue_idle_timeout_ms:
            raise ValueError(
                "task_timeout_s는 queue_idle_timeout_ms보다 짧아야 한다(신뢰성 설계 2절)"
            )
        return self


def load_settings() -> Settings:
    return Settings()
