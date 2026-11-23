import psycopg
import pytest
from alembic import command
from sqlalchemy.engine import make_url

from tests.conftest import alembic_config


def _extensions(url: str) -> set[str]:
    parsed = make_url(url)
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conninfo) as conn:
        return {row[0] for row in conn.execute("SELECT extname FROM pg_extension")}


@pytest.mark.db
def test_upgrade_downgrade_upgrade(database_url: str) -> None:
    config = alembic_config(database_url)
    assert {"vector", "pg_trgm"} <= _extensions(database_url)
    command.downgrade(config, "base")
    assert not {"vector", "pg_trgm"} & _extensions(database_url)
    command.upgrade(config, "head")
    assert {"vector", "pg_trgm"} <= _extensions(database_url)
    command.check(config)  # model và migration không lệch nhau
