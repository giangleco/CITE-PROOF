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


@pytest.mark.db
def test_0010_maps_legacy_access_status(database_url: str) -> None:
    config = alembic_config(database_url)
    parsed = make_url(database_url)
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    command.downgrade(config, "0009")
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute("TRUNCATE users, source_files CASCADE")
            conn.execute(
                "WITH u AS (INSERT INTO users (email, password_hash, display_name) "
                "VALUES ('m@b.vn', 'x', 'M') RETURNING id), "
                "d AS (INSERT INTO documents (user_id, kind, filename, mime_type, storage_key, "
                "sha256) SELECT id, 'TEXT', 'a', 'text/plain', 'k', 's' FROM u RETURNING id, "
                "user_id), "
                "r AS (INSERT INTO reference_entries (document_id, order_index, raw_text, "
                "ref_type, parse_method) SELECT id, 0, 'r', 'ARTICLE', 'rule' FROM d "
                "RETURNING id) "
                "INSERT INTO source_files (origin, access_status, content_level, storage_key, "
                "sha256, title_match, owner_user_id, reference_entry_id) "
                "SELECT 'USER_UPLOAD', 'UPLOADED', 'FULL_TEXT', 'u', 'u', 'UNKNOWN', "
                "d.user_id, r.id FROM d, r"
            )
            conn.execute(
                "INSERT INTO source_files (origin, access_status, content_level, storage_key, "
                "sha256, title_match) VALUES "
                "('OPEN_ACCESS', 'OPEN', 'ABSTRACT', 'a', 'a', 'UNKNOWN'), "
                "('OPEN_ACCESS', 'OPEN', 'FULL_TEXT', 'f', 'f', 'UNKNOWN'), "
                "('OPEN_ACCESS', 'OPEN_ABSTRACT', 'FULL_TEXT', 'k', 'k', 'UNKNOWN')"
            )
        command.upgrade(config, "head")
        with psycopg.connect(conninfo, autocommit=True) as conn:
            rows = sorted(conn.execute("SELECT sha256, access_status FROM source_files").fetchall())
            conn.execute("TRUNCATE users, source_files CASCADE")
    finally:
        command.upgrade(config, "head")
    assert rows == [
        ("a", "OPEN_ABSTRACT"),
        ("f", "OPEN_FULL_TEXT"),
        ("k", "OPEN_ABSTRACT"),  # giá trị đã hợp lệ giữ nguyên
        ("u", "USER_PROVIDED"),
    ]


@pytest.mark.db
def test_0011_adds_nullable_consent_provider(database_url: str) -> None:
    config = alembic_config(database_url)
    parsed = make_url(database_url)
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    command.downgrade(config, "0010")
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, consent_llm_at) "
            "VALUES ('m11@x.vn', 'x', 'M', now())"
        )
    command.upgrade(config, "0011")
    with psycopg.connect(conninfo, autocommit=True) as conn:
        row = conn.execute(
            "SELECT consent_llm_provider FROM users WHERE email = 'm11@x.vn'"
        ).fetchone()
        assert row == (None,)
        conn.execute("DELETE FROM users WHERE email = 'm11@x.vn'")
    command.downgrade(config, "0010")
    command.upgrade(config, "head")
