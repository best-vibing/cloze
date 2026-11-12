"""alembic 명령이 실행하는 파일. 주소는 Settings에서, 테이블 정의는 Base.metadata에서 읽는다.

앱은 비동기 엔진을 쓰지만 마이그레이션은 동기로 돈다. psycopg 하나가 둘 다 지원하므로
같은 주소를 그대로 쓴다.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from cloze.config import load_settings
from cloze.infra.db import Base

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

url = load_settings().database_url.get_secret_value()

if context.is_offline_mode():
    # `alembic upgrade head --sql`: DB에 붙지 않고 실행할 SQL만 출력한다.
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, poolclass=pool.NullPool).connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
