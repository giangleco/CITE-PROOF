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


_TRUNCATE = "TRUNCATE users, works, source_files CASCADE"


def _conninfo(database_url: str) -> str:
    parsed = make_url(database_url)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _seed_base(conn: psycopg.Connection) -> dict[str, object]:
    conn.execute(_TRUNCATE)
    base: dict[str, object] = {}
    base["work"] = _one(
        conn,
        "INSERT INTO works (doi, title, title_norm, metadata_source) "
        "VALUES ('10.1/g', 'T', 't', 'crossref') RETURNING id",
    )
    user = _one(
        conn,
        "INSERT INTO users (email, password_hash, display_name) VALUES ('g@b.vn', 'x', 'G') "
        "RETURNING id",
    )
    doc = _one(
        conn,
        "INSERT INTO documents (user_id, kind, filename, mime_type, storage_key, sha256) "
        "VALUES (%s, 'TEXT', 'a', 'text/plain', 'k', 's') RETURNING id",
        (user,),
    )
    run = _one(
        conn,
        "INSERT INTO verification_runs (document_id, user_id) VALUES (%s, %s) RETURNING id",
        (doc, user),
    )
    citation = _one(
        conn,
        "INSERT INTO citations (document_id, marker_text, sentence_text, char_start, char_end) "
        "VALUES (%s, '[1]', 's', 0, 1) RETURNING id",
        (doc,),
    )
    claim = _one(
        conn,
        "INSERT INTO claims (document_id, citation_id, claim_text, context_text, language, "
        "extraction_method) VALUES (%s, %s, 'c', 'c', 'en', 'rule') RETURNING id",
        (doc, citation),
    )
    base["verification"] = _one(
        conn,
        "INSERT INTO claim_verifications (run_id, claim_id, status, content_level_used, "
        "explanation, model, prompt_version, schema_version, verifier, unverifiable_reason) "
        "VALUES (%s, %s, 'SUPPORTED', 'FULL_TEXT', 'e', 'm', 'p', 's', 'V0', "
        "'REFERENCE_UNRESOLVED') RETURNING id",
        (run, claim),
    )
    return base


def _file(
    conn: psycopg.Connection,
    work: object,
    key: str,
    created: str,
    origin: str = "OPEN_ACCESS",
) -> object:
    if origin == "OPEN_ACCESS":
        return _one(
            conn,
            "INSERT INTO source_files (work_id, origin, access_status, content_level, "
            "storage_key, sha256, title_match, created_at) VALUES "
            "(%s, 'OPEN_ACCESS', 'OPEN', 'FULL_TEXT', %s, 'same', 'UNKNOWN', %s) RETURNING id",
            (work, key, created),
        )
    return _one(
        conn,
        "WITH r AS (INSERT INTO reference_entries (document_id, order_index, raw_text, "
        "ref_type, parse_method) SELECT id, 0, 'r', 'ARTICLE', 'rule' FROM documents "
        "LIMIT 1 RETURNING id) "
        "INSERT INTO source_files (work_id, origin, access_status, content_level, storage_key, "
        "sha256, title_match, created_at, owner_user_id, reference_entry_id) "
        "SELECT %s, 'USER_UPLOAD', 'UPLOADED', 'FULL_TEXT', %s, 'same', 'UNKNOWN', %s, "
        "(SELECT id FROM users LIMIT 1), r.id FROM r RETURNING id",
        (work, key, created),
    )


def _chunk(conn: psycopg.Connection, file_id: object, index: int, text: str) -> object:
    return _one(
        conn,
        "INSERT INTO source_chunks (source_file_id, chunk_index, text, char_start, char_end, "
        "region, language) VALUES (%s, %s, %s, 0, 1, 'BODY', 'en') RETURNING id",
        (file_id, index, text),
    )


def _evidence(conn: psycopg.Connection, verification: object, chunk: object) -> None:
    conn.execute(
        "INSERT INTO evidence (claim_verification_id, source_chunk_id, relation, quote_text, "
        "char_start, char_end) VALUES (%s, %s, 'SUPPORTS', 'q', 0, 1)",
        (verification, chunk),
    )


def _upgrade_outcome(config: object, conninfo: str) -> tuple[Exception | None, list[tuple[str]]]:
    """Nâng lên head; trả về (lỗi nếu có, storage_key còn lại), rồi dọn và đưa DB về head."""
    error: Exception | None = None
    try:
        command.upgrade(config, "head")  # type: ignore[arg-type]
    except Exception as exc:
        error = exc
    with psycopg.connect(conninfo, autocommit=True) as conn:
        left = sorted(conn.execute("SELECT storage_key FROM source_files").fetchall())
        conn.execute(_TRUNCATE)
    command.upgrade(config, "head")  # type: ignore[arg-type]
    return error, left


@pytest.mark.db
def test_0010_aborts_when_chunk_text_differs(database_url: str) -> None:
    config = alembic_config(database_url)
    conninfo = _conninfo(database_url)
    command.downgrade(config, "0009")
    with psycopg.connect(conninfo, autocommit=True) as conn:
        base = _seed_base(conn)
        early = _file(conn, base["work"], "early", "2026-01-01")
        late = _file(conn, base["work"], "late", "2026-02-01")
        _chunk(conn, early, 0, "new chunker text")
        _evidence(conn, base["verification"], _chunk(conn, late, 0, "old chunker text"))
    error, left = _upgrade_outcome(config, conninfo)
    assert error is not None and "sha256=same" in str(error)
    assert left == [("early",), ("late",)]  # không xóa gì


@pytest.mark.db
def test_0010_aborts_when_evidence_chunk_has_no_counterpart(database_url: str) -> None:
    config = alembic_config(database_url)
    conninfo = _conninfo(database_url)
    command.downgrade(config, "0009")
    with psycopg.connect(conninfo, autocommit=True) as conn:
        base = _seed_base(conn)
        early = _file(conn, base["work"], "early", "2026-01-01")
        late = _file(conn, base["work"], "late", "2026-02-01")
        _chunk(conn, early, 0, "a")
        _evidence(conn, base["verification"], _chunk(conn, late, 5, "a"))
    error, left = _upgrade_outcome(config, conninfo)
    assert error is not None and "sha256=same" in str(error)
    assert left == [("early",), ("late",)]


@pytest.mark.db
def test_0010_dedupes_three_copies_and_ignores_null_work_and_uploads(
    database_url: str,
) -> None:
    config = alembic_config(database_url)
    conninfo = _conninfo(database_url)
    command.downgrade(config, "0009")
    with psycopg.connect(conninfo, autocommit=True) as conn:
        base = _seed_base(conn)
        work = base["work"]
        a = _file(conn, work, "a", "2026-03-01")
        b = _file(conn, work, "b", "2026-01-01")
        c = _file(conn, work, "c", "2026-02-01")
        for file_id in (a, b, c):
            chunk = _chunk(conn, file_id, 0, "same text")
            if file_id != b:
                _evidence(conn, base["verification"], chunk)
        # Hàng không thuộc nhóm trùng: work_id NULL (hai dòng) và USER_UPLOAD (hai dòng).
        _file(conn, None, "n1", "2026-01-01")
        _file(conn, None, "n2", "2026-01-02")
        _file(conn, work, "u1", "2026-01-01", origin="USER_UPLOAD")
        _file(conn, work, "u2", "2026-01-02", origin="USER_UPLOAD")
    error, left = _upgrade_outcome(config, conninfo)
    assert error is None
    assert left == [("b",), ("n1",), ("n2",), ("u1",), ("u2",)]
