"""Môi trường Alembic (đồng bộ, driver psycopg)."""

from alembic import context
from sqlalchemy import create_engine, pool

import citeproof.db.models  # noqa: F401  (đăng ký bảng lên Base.metadata)
from citeproof.core.settings import get_settings
from citeproof.db.base import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("url")
    return override or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
