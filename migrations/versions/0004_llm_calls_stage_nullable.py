"""llm_calls.stage nullable

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("llm_calls", "stage", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("llm_calls", "stage", existing_type=sa.Text(), nullable=False)
