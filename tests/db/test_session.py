import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.db
@pytest.mark.usefixtures("clean_db")
async def test_session_factory_executes_query(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
