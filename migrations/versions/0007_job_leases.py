"""jobs: lease gia hạn được (heartbeat) và thời điểm bắt đầu/kết thúc

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-24 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_jobs_status_lease_expires_at",
        "jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )
    # Job đang chạy giữ hạn khóa cũ (900 s tính từ locked_at) để không bị nhận lại sớm.
    op.execute(
        "UPDATE jobs SET lease_expires_at = locked_at + interval '900 seconds' "
        "WHERE status = 'RUNNING'"
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_status_lease_expires_at", table_name="jobs")
    op.drop_column("jobs", "finished_at")
    op.drop_column("jobs", "started_at")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "heartbeat_at")
