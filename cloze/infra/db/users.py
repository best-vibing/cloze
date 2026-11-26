"""users 테이블과 쿼리 함수. 필드는 docs/design/data-contract.md 4절 users 행이다.

커밋은 호출하는 쪽이 한다. 계정은 API가 아니라 CLI로 만든다(ADR-007).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from cloze.infra.db import Base
from cloze.infra.passwords import hash_password


class UserRow(Base):
    __tablename__ = "users"

    # ADR-006: 모든 ID는 무작위 UUID다.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    login_id: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    # 세션 쿠키에 함께 넣는 값. 비밀번호를 바꾸면 올려서 기존 세션을 모두 끊는다.
    session_version: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> UserRow | None:
    return await session.get(UserRow, user_id)


async def get_user_by_login_id(session: AsyncSession, login_id: str) -> UserRow | None:
    result = await session.execute(select(UserRow).where(UserRow.login_id == login_id))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession, login_id: str, password: str, display_name: str
) -> UserRow:
    """같은 login_id가 있으면 flush에서 IntegrityError가 난다."""
    user = UserRow(
        login_id=login_id, password_hash=hash_password(password), display_name=display_name
    )
    session.add(user)
    await session.flush()
    return user


async def set_password(session: AsyncSession, login_id: str, password: str) -> bool:
    """비밀번호를 바꾸고 session_version을 올린다. 계정이 없으면 False."""
    user = await get_user_by_login_id(session, login_id)
    if user is None:
        return False
    user.password_hash = hash_password(password)
    user.session_version += 1
    await session.flush()
    return True
