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


def upgrade() -> None:
    allowed = ", ".join(f"'{value}'" for value in _VALUES)
    op.execute(
        f"UPDATE {_TABLE} SET access_status = CASE "
        "WHEN origin = 'USER_UPLOAD' THEN 'USER_PROVIDED' "
        "WHEN content_level = 'ABSTRACT' THEN 'OPEN_ABSTRACT' "
        "ELSE 'OPEN_FULL_TEXT' END "
        f"WHERE access_status NOT IN ({allowed})"
    )
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
