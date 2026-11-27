import psycopg
import pytest
from alembic import command
from sqlalchemy.engine import make_url

from tests.conftest import alembic_config


def _one(conn: psycopg.Connection, sql: str, params: tuple[object, ...] = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.db
def test_0010_dedupes_open_access_and_repoints_references(database_url: str) -> None:
    config = alembic_config(database_url)
    parsed = make_url(database_url)
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    command.downgrade(config, "0009")
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute("TRUNCATE users, works, source_files CASCADE")
            work = _one(
                conn,
                "INSERT INTO works (doi, title, title_norm, metadata_source) "
                "VALUES ('10.1/dup', 'T', 't', 'crossref') RETURNING id",
            )
            files: dict[str, object] = {}
            chunks: dict[str, object] = {}
            # Hai bản trùng (work, sha256): "early" cũ hơn nên được giữ; "late" bị xóa.
            for key, created in (("late", "2026-02-01"), ("early", "2026-01-01")):
                files[key] = _one(
                    conn,
                    "INSERT INTO source_files (work_id, origin, access_status, content_level, "
                    "storage_key, sha256, title_match, created_at) VALUES "
                    "(%s, 'OPEN_ACCESS', 'OPEN', 'FULL_TEXT', %s, 'same', 'UNKNOWN', %s) "
                    "RETURNING id",
                    (work, key, created),
                )
                chunks[key] = _one(
                    conn,
                    "INSERT INTO source_chunks (source_file_id, chunk_index, text, char_start, "
                    "char_end, region, language) VALUES (%s, 0, 'x', 0, 1, 'BODY', 'en') "
                    "RETURNING id",
                    (files[key],),
                )
            user = _one(
                conn,
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES ('d@b.vn', 'x', 'D') RETURNING id",
            )
            doc = _one(
                conn,
                "INSERT INTO documents (user_id, kind, filename, mime_type, storage_key, "
                "sha256) VALUES (%s, 'TEXT', 'a', 'text/plain', 'k', 's') RETURNING id",
                (user,),
            )
            run = _one(
                conn,
                "INSERT INTO verification_runs (document_id, user_id) VALUES (%s, %s) RETURNING id",
                (doc, user),
            )
            citation = _one(
                conn,
                "INSERT INTO citations (document_id, marker_text, sentence_text, char_start, "
                "char_end) VALUES (%s, '[1]', 's', 0, 1) RETURNING id",
                (doc,),
            )
            claim = _one(
                conn,
                "INSERT INTO claims (document_id, citation_id, claim_text, context_text, "
                "language, extraction_method) VALUES (%s, %s, 'c', 'c', 'en', 'rule') "
                "RETURNING id",
                (doc, citation),
            )
            verification = _one(
                conn,
                "INSERT INTO claim_verifications (run_id, claim_id, status, content_level_used, "
                "explanation, model, prompt_version, schema_version, verifier, "
                "unverifiable_reason) VALUES (%s, %s, 'SUPPORTED', 'FULL_TEXT', 'e', 'm', 'p', "
                "'s', 'V0', 'REFERENCE_UNRESOLVED') RETURNING id",
                (run, claim),
            )
            conn.execute(
                "INSERT INTO evidence (claim_verification_id, source_chunk_id, relation, "
                "quote_text, char_start, char_end) VALUES (%s, %s, 'SUPPORTS', 'q', 0, 1)",
                (verification, chunks["late"]),
            )
            # Cùng khóa duy nhất sau khi chuyển: dòng của bản bị xóa va chạm với dòng đã có.
            for key in ("late", "early"):
                conn.execute(
                    "INSERT INTO retrieval_results (run_id, claim_id, source_file_id, "
                    "source_chunk_id, rank, query_variant) VALUES (%s, %s, %s, %s, 1, 'v')",
                    (run, claim, files[key], chunks[key]),
                )
            conn.execute(
                "INSERT INTO retrieval_results (run_id, claim_id, source_file_id, "
                "source_chunk_id, rank, query_variant) VALUES (%s, %s, %s, %s, 2, 'w')",
                (run, claim, files["late"], chunks["late"]),
            )
    except Exception:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute("TRUNCATE users, works, source_files CASCADE")
        command.upgrade(config, "head")
        raise
    try:
        command.upgrade(config, "head")
        with psycopg.connect(conninfo, autocommit=True) as conn:
            kept = conn.execute("SELECT id, storage_key FROM source_files").fetchall()
            chunk_rows = conn.execute("SELECT id, source_file_id FROM source_chunks").fetchall()
            evidence = conn.execute("SELECT source_chunk_id FROM evidence").fetchall()
            retrieval = sorted(
                conn.execute(
                    "SELECT query_variant, source_file_id, source_chunk_id FROM retrieval_results"
                ).fetchall()
            )
    finally:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute("TRUNCATE users, works, source_files CASCADE")
        command.upgrade(config, "head")
    assert kept == [(files["early"], "early")]
    assert chunk_rows == [(chunks["early"], files["early"])]
    assert evidence == [(chunks["early"],)]
    assert retrieval == [
        ("v", files["early"], chunks["early"]),  # va chạm: gộp còn một dòng
        ("w", files["early"], chunks["early"]),  # chuyển sang bản giữ lại
    ]
