import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.session import make_engine


@pytest.mark.db
@pytest.mark.usefixtures("clean_db")
async def test_session_factory_executes_query(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1


def test_engine_hides_sql_parameters() -> None:
    engine = make_engine("postgresql+psycopg://u:p@localhost:5432/db")
    assert engine.sync_engine.hide_parameters is True
