"""환경 변수에서 설정을 읽는다. 모든 변수는 CLOZE_ 로 시작한다."""

from pathlib import Path

from pydantic import SecretStr
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
    # 서비스 주소. 상태를 바꾸는 요청은 Origin 헤더가 이 값일 때만 받는다(CSRF 방어).
    public_origin: str = "http://localhost:8000"
    # 웹 페이지 빌드 결과 폴더. frontend/ 는 빌드 전 소스라 내보내지 않는다.
    # 없으면 API만 띄운다.
    static_dir: Path = Path("frontend/dist")
    # 업로드 한도와 검사 하위 프로세스 제한(신뢰성 설계 2·5절, [가설]).
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_pages: int = 30
    upload_probe_timeout_s: float = 5.0
    upload_probe_memory_mb: int = 256


def load_settings() -> Settings:
    return Settings()
