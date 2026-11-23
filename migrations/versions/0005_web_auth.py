"""web auth: invites, web_sessions, login_attempts

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_created() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.Uuid(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        *_id_created(),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_invites_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["used_by"], ["users.id"], name=op.f("fk_invites_used_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_invites_code_hash")),
    )
    op.create_index(op.f("ix_invites_created_by"), "invites", ["created_by"], unique=False)
    op.create_index(op.f("ix_invites_used_by"), "invites", ["used_by"], unique=False)

    op.create_table(
        "web_sessions",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_id_created(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_web_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_web_sessions_token_hash")),
    )
    op.create_index(op.f("ix_web_sessions_user_id"), "web_sessions", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_web_sessions_expires_at"), "web_sessions", ["expires_at"], unique=False
    )

    op.create_table(
        "login_attempts",
        sa.Column("email_norm", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        *_id_created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_attempts")),
    )
    op.create_index(
        "ix_login_attempts_email_norm_created_at",
        "login_attempts",
        ["email_norm", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_login_attempts_ip_created_at", "login_attempts", ["ip", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_table("login_attempts")
    op.drop_table("web_sessions")
    op.drop_table("invites")
