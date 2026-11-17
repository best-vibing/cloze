"""PostgreSQL 접속. 테이블 클래스는 `Base`를 상속하고, 세션은 `make_sessionmaker`로 만든다."""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from cloze.config import Settings

# 제약·인덱스 이름을 규칙으로 정해 두면 마이그레이션 파일에 DB마다 다른 자동 이름이 박히지 않는다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(settings: Settings) -> AsyncEngine:
    # libpq는 기본으로 연결을 무한정 기다린다. DB가 응답하지 않으면 멈추지 않고 실패하게 한다.
    return create_async_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 5}
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # 비동기 세션은 속성을 읽을 때 몰래 DB를 조회(지연 로딩)할 수 없다.
    # 커밋 뒤에 값을 읽어도 다시 조회하지 않도록 만료를 끈다.
    return async_sessionmaker(engine, expire_on_commit=False)
