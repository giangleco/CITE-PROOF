"""source_files.access_status theo enum; bản mở duy nhất theo (work_id, sha256)

Spec lõi QĐ-18, §7.5, §14: CHECK `access_status` IN (`OPEN_FULL_TEXT`, `OPEN_ABSTRACT`,
`USER_PROVIDED`); index duy nhất một phần (`work_id`, `sha256`) WHERE `origin = 'OPEN_ACCESS'`
để S5 chèn bản mở dùng chung với `ON CONFLICT DO NOTHING`. Giá trị cũ ngoài enum (trước đây cột
không bị ràng buộc) được ánh xạ theo nguồn gốc và mức nội dung trước khi thêm CHECK.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-24 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "source_files"
_CHECK = "ck_source_files_access_status_enum"
_INDEX = "uq_source_files_open_work_sha"
_VALUES = ("OPEN_FULL_TEXT", "OPEN_ABSTRACT", "USER_PROVIDED")


def _dedupe_open_access() -> None:
    """Gộp các bản mở trùng (work_id, sha256) về dòng `created_at` sớm nhất trước khi tạo index.

    Mọi FK tới `source_files`/`source_chunks` đều ON DELETE CASCADE, nên xóa thẳng sẽ mất
    bằng chứng. Chuyển `evidence` và `retrieval_results` sang chunk của dòng giữ lại chỉ khi
    cùng `chunk_index` VÀ cùng `text` (bản chia chunk khác phiên bản sẽ làm lệch quote/offset).
    Còn tham chiếu không ánh xạ được thì hủy migration để người vận hành xử lý tay.

    Không đảo ngược được: `downgrade` không khôi phục các dòng trùng đã xóa, và các blob
    lưu trữ (`storage_key`) của chúng bị bỏ mồ côi.
    """
    op.execute(
        "CREATE TEMP TABLE _dup_source_files ON COMMIT DROP AS "
        "SELECT id, keep_id FROM ("
        "SELECT id, first_value(id) OVER w AS keep_id, row_number() OVER w AS rn "
        f"FROM {_TABLE} WHERE origin = 'OPEN_ACCESS' AND work_id IS NOT NULL "
        "WINDOW w AS (PARTITION BY work_id, sha256 ORDER BY created_at, id)"
        ") ranked WHERE rn > 1"
    )
    op.execute(
        "CREATE TEMP TABLE _dup_chunk_map ON COMMIT DROP AS "
        "SELECT dc.id AS old_id, kc.id AS new_id, d.keep_id "
        "FROM _dup_source_files d "
        "JOIN source_chunks dc ON dc.source_file_id = d.id "
        "JOIN source_chunks kc ON kc.source_file_id = d.keep_id "
        "AND kc.chunk_index = dc.chunk_index AND kc.text = dc.text"
    )
    _abort_if_unmapped_references()
    op.execute(
        "UPDATE evidence SET source_chunk_id = m.new_id "
        "FROM _dup_chunk_map m WHERE evidence.source_chunk_id = m.old_id"
    )
    # retrieval_results có UNIQUE (run, claim, chunk, variant): bỏ dòng sẽ va chạm.
    op.execute(
        "DELETE FROM retrieval_results r USING ("
        "SELECT r2.id, m.new_id, "
        "row_number() OVER (PARTITION BY r2.run_id, r2.claim_id, m.new_id, r2.query_variant "
        "ORDER BY r2.id) AS rn "
        "FROM retrieval_results r2 JOIN _dup_chunk_map m ON r2.source_chunk_id = m.old_id"
        ") x WHERE r.id = x.id AND (x.rn > 1 OR EXISTS ("
        "SELECT 1 FROM retrieval_results e WHERE e.run_id = r.run_id AND e.claim_id = r.claim_id "
        "AND e.source_chunk_id = x.new_id AND e.query_variant = r.query_variant))"
    )
    op.execute(
        "UPDATE retrieval_results SET source_chunk_id = m.new_id, source_file_id = m.keep_id "
        "FROM _dup_chunk_map m WHERE retrieval_results.source_chunk_id = m.old_id"
    )
    op.execute(f"DELETE FROM {_TABLE} WHERE id IN (SELECT id FROM _dup_source_files)")
    op.execute("DROP TABLE _dup_chunk_map")
    op.execute("DROP TABLE _dup_source_files")


def _abort_if_unmapped_references() -> None:
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT DISTINCT f.work_id, f.sha256 FROM _dup_source_files d "
                "JOIN source_files f ON f.id = d.id "
                "JOIN source_chunks c ON c.source_file_id = d.id "
                "WHERE c.id NOT IN (SELECT old_id FROM _dup_chunk_map) AND ("
                "EXISTS (SELECT 1 FROM evidence e WHERE e.source_chunk_id = c.id) OR "
                "EXISTS (SELECT 1 FROM retrieval_results r WHERE r.source_chunk_id = c.id))"
            )
        )
        .all()
    )
    if rows:
        groups = ", ".join(f"(work_id={w}, sha256={h})" for w, h in rows)
        raise RuntimeError(
            "0010: bản mở trùng có evidence/retrieval_results không ánh xạ được sang chunk "
            f"của bản giữ lại (khác chunk_index hoặc text); xử lý tay các nhóm: {groups}"
        )


def upgrade() -> None:
    allowed = ", ".join(f"'{value}'" for value in _VALUES)
    op.execute(
        f"UPDATE {_TABLE} SET access_status = CASE "
        "WHEN origin = 'USER_UPLOAD' THEN 'USER_PROVIDED' "
        "WHEN content_level = 'ABSTRACT' THEN 'OPEN_ABSTRACT' "
        "ELSE 'OPEN_FULL_TEXT' END "
        f"WHERE access_status NOT IN ({allowed})"
    )
    _dedupe_open_access()
    op.create_check_constraint(op.f(_CHECK), _TABLE, f"access_status IN ({allowed})")
    op.create_index(
        op.f(_INDEX),
        _TABLE,
        ["work_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("origin = 'OPEN_ACCESS'"),
    )


def downgrade() -> None:
    op.drop_index(op.f(_INDEX), table_name=_TABLE)
    op.drop_constraint(op.f(_CHECK), _TABLE, type_="check")
