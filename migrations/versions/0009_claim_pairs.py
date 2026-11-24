"""claims.is_claim; claim_verifications theo cặp claim-reference, reference NULL khi chưa liên kết

Spec lõi QĐ-14, QĐ-15: `claims.is_claim` (mặc định true); `claim_verifications.reference_entry_id`
được NULL chỉ khi lý do là `REFERENCE_UNRESOLVED`; khóa duy nhất thành
(`run_id`, `claim_id`, `reference_entry_id`, `verifier`) NULLS NOT DISTINCT (PostgreSQL 15+).
Khóa mới sinh cùng tên với khóa cũ theo quy ước đặt tên (`uq_<bảng>_<cột đầu>`).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-24 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "claim_verifications"
_UNIQUE = "uq_claim_verifications_run_id"
_UNRESOLVED_CHECK = "ck_claim_verifications_unresolved_needs_reason"


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column("is_claim", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.alter_column(_TABLE, "reference_entry_id", existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint(
        op.f(_UNRESOLVED_CHECK),
        _TABLE,
        # NULL-safe: với `=` thì lý do NULL cho biểu thức NULL và CHECK vẫn đạt.
        "reference_entry_id IS NOT NULL "
        "OR unverifiable_reason IS NOT DISTINCT FROM 'REFERENCE_UNRESOLVED'",
    )
    op.drop_constraint(op.f(_UNIQUE), _TABLE, type_="unique")
    op.create_unique_constraint(
        op.f(_UNIQUE),
        _TABLE,
        ["run_id", "claim_id", "reference_entry_id", "verifier"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    # Lược đồ cũ không chứa được cặp chưa liên kết hay hai reference cho cùng claim và
    # verifier: bỏ các dòng đó (giữ dòng tạo sớm nhất) trước khi dựng lại khóa cũ.
    op.drop_constraint(op.f(_UNIQUE), _TABLE, type_="unique")
    op.execute(f"DELETE FROM {_TABLE} WHERE reference_entry_id IS NULL")
    op.execute(
        f"DELETE FROM {_TABLE} AS a USING {_TABLE} AS b "
        "WHERE a.run_id = b.run_id AND a.claim_id = b.claim_id AND a.verifier = b.verifier "
        "AND (a.created_at, a.id) > (b.created_at, b.id)"
    )
    op.create_unique_constraint(op.f(_UNIQUE), _TABLE, ["run_id", "claim_id", "verifier"])
    op.drop_constraint(op.f(_UNRESOLVED_CHECK), _TABLE, type_="check")
    op.alter_column(_TABLE, "reference_entry_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("claims", "is_claim")
