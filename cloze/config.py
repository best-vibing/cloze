"""환경 변수에서 설정을 읽는다. 모든 변수는 CLOZE_ 로 시작한다."""

from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOZE_", env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
    gemini_max_concurrency: int = Field(default=4, gt=0)
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_s: float = 60.0
    # 기준 PDF 세트 위치. 저작권 때문에 레포에 두지 않고, 없으면 해당 테스트를 건너뛴다.
    samples_dir: Path | None = None
    # 기본값은 로컬 개발용(docker-compose.yml의 postgres·redis). 운영 주소는 .env로 넣는다.
    # localhost가 아니라 127.0.0.1이다. compose가 127.0.0.1에만 포트를 열어서, localhost가
    # ::1로 먼저 풀리면 접속하지 못한다(제한 시간이 없는 연결은 그대로 멈춘다).
    database_url: SecretStr = SecretStr("postgresql+psycopg://cloze:cloze@127.0.0.1:5432/cloze")
    redis_url: str = "redis://127.0.0.1:6379/0"
    # 신뢰성 설계 2절. 작업 제한 시간이 idle_timeout 이상이면 살아서 처리 중인 작업이
    # 다른 워커로 넘어가 두 번 실행된다.
    task_timeout_s: int = 480
    queue_idle_timeout_ms: int = 900_000
    # 서비스 주소. 상태를 바꾸는 요청은 Origin 헤더가 이 값일 때만 받는다(CSRF 방어).
    public_origin: str = "http://localhost:8000"
    # 웹 페이지 빌드 결과 폴더. frontend/ 는 빌드 전 소스라 내보내지 않는다.
    # 없으면 API만 띄운다.
    static_dir: Path = Path("frontend/dist")
    # 세션 쿠키 서명 키. 기본값을 두지 않는다: 없으면 API 앱이 뜨지 않는다(create_app).
    # 워커·alembic은 쓰지 않으므로 여기서는 필수로 두지 않는다.
    session_secret: SecretStr | None = None
    # 쿠키 Secure 속성. https가 아닌 로컬 개발에서만 끈다(데이터 계약 2절 COOKIE_SECURE).
    cookie_secure: bool = True
    # 로그인 실패 한도: 같은 아이디로 창 안에 이만큼 틀리면 막는다(신뢰성 설계 5절, [가설]).
    login_fail_limit: int = 5
    login_fail_window_s: float = 60.0
    # 업로드 원본·이미지를 두는 폴더(로컬 디스크 저장소). 레포에는 커밋하지 않는다.
    # API·워커·정리 작업이 같은 폴더를 봐야 하므로 실행 위치와 무관한 절대 경로만 받는다.
    storage_dir: Path = REPO_ROOT / "data" / "storage"

    @model_validator(mode="after")
    def _timeout_below_idle(self) -> "Settings":
        if self.task_timeout_s * 1000 >= self.queue_idle_timeout_ms:
            raise ValueError(
                "task_timeout_s는 queue_idle_timeout_ms보다 짧아야 한다(신뢰성 설계 2절)"
            )
        return self

    @model_validator(mode="after")
    def _storage_dir_absolute(self) -> "Settings":
        # 상대 경로면 프로세스마다 작업 폴더 기준으로 풀려 서로 다른 저장소를 본다(#100 리뷰).
        if not self.storage_dir.is_absolute():
            raise ValueError("storage_dir는 절대 경로여야 한다")
        return self


def load_settings() -> Settings:
    return Settings()
