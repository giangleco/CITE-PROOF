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


@pytest.mark.db
def test_0008_normalizes_sample_dois(database_url: str) -> None:
    config = alembic_config(database_url)
    parsed = make_url(database_url)
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    command.downgrade(config, "0007")
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute("TRUNCATE works CASCADE")
            conn.execute(
                "INSERT INTO works (doi, title, title_norm, metadata_source) VALUES "
                "('10.48550/arXiv.1706.03762', 'A', 'a', 'sample'), "
                "('10.18653/v1/N19-1423', 'B', 'b', 'sample'), "
                "('10.18653/v1/n19-1423', 'B', 'b', 'crossref'), "
                "('10.1000/KEEP', 'C', 'c', 'crossref')"
            )
        command.upgrade(config, "head")
        with psycopg.connect(conninfo, autocommit=True) as conn:
            rows = sorted(conn.execute("SELECT doi, metadata_source FROM works").fetchall())
            conn.execute("TRUNCATE works CASCADE")
    finally:
        command.upgrade(config, "head")
    assert rows == sorted(
        [
            ("10.1000/KEEP", "crossref"),  # chỉ dòng bài mẫu được chuẩn hóa
            ("10.18653/v1/N19-1423", "sample"),  # đã có dòng thật mang DOI chuẩn hóa: giữ nguyên
            ("10.18653/v1/n19-1423", "crossref"),
            ("10.48550/arxiv.1706.03762", "sample"),
        ]
    )
