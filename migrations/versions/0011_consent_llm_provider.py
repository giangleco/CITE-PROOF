"""users.consent_llm_provider: provider LLM mà người dùng đã đồng ý

Spec §5.4a: đổi provider thì phải đồng ý lại. Cột NULL cho đồng ý trước migration này
(không backfill; quy tắc đối chiếu nằm ở `auth.needs_consent`).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-27 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("consent_llm_provider", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "consent_llm_provider")
