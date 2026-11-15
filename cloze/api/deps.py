"""라우터들이 같이 쓰는 의존성."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """요청마다 세션 하나. 커밋은 라우터가 한다."""
    async with request.app.state.sessions() as session:
        yield session
