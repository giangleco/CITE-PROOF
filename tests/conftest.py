"""Fixture dùng chung: database test, engine, session và dọn logging."""

import argparse
import logging
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import citeproof.db.models  # noqa: F401  (đăng ký bảng lên Base.metadata)
from citeproof.core.settings import Settings
from citeproof.db.base import Base
from citeproof.db.session import make_engine, make_session_factory
from tests.golden.cases import GoldenCase, load_cases

DEFAULT_TEST_URL = "postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof_test"
_HANDLER_MARK = "_citeproof_handler"


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    return config


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    level = root.level
    yield
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("CITEPROOF_TEST_DATABASE_URL", DEFAULT_TEST_URL)
    parsed = make_url(url)
    admin_dsn = make_conninfo(
        host=parsed.host or "localhost",
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        dbname="postgres",
    )
    db_name = parsed.database or "citeproof_test"
    if not db_name.endswith("_test"):
        # Fixture này DROP database: không bao giờ đụng tới database không phải test.
        pytest.fail(
            f"Từ chối dùng database {db_name!r}: tên database test phải kết thúc bằng '_test'"
        )
    name = sql.Identifier(db_name)
    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
            conn.execute(sql.SQL("CREATE DATABASE {}").format(name))
    except psycopg.OperationalError as exc:
        if os.environ.get("CITEPROOF_REQUIRE_DB") == "1":
            pytest.fail(f"PostgreSQL không truy cập được: {exc}")
        pytest.skip("PostgreSQL không truy cập được; bỏ qua test db")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> AsyncEngine:
    return make_engine(database_url)


@pytest.fixture(scope="session")
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_session_factory(engine)


@pytest.fixture
async def clean_db(engine: AsyncEngine) -> AsyncIterator[None]:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if tables:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield
    # Engine dùng chung giữa các event loop theo test: bỏ connection đã pool.
    await engine.dispose()


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    """Settings môi trường local dùng DB test và thư mục storage tạm."""
    return Settings(database_url=database_url, env="local", storage_dir=tmp_path / "storage")


_GOLDEN_WITH_EXPECTED = [case for case in load_cases() if case.expected is not None]


@pytest.fixture(params=_GOLDEN_WITH_EXPECTED, ids=lambda case: case.name)
def golden_case(request: pytest.FixtureRequest) -> GoldenCase:
    """Mỗi mẫu golden có kết quả mong đợi (bỏ `encrypted`, `image_only`)."""
    case: GoldenCase = request.param
    return case


@pytest.fixture(
    params=[case for case in _GOLDEN_WITH_EXPECTED if case.input_path.suffix == ".docx"],
    ids=lambda case: case.name,
)
def golden_docx_case(request: pytest.FixtureRequest) -> GoldenCase:
    """Chỉ các mẫu golden DOCX (T5 kiểm tra ngôn ngữ, cảnh báo, section)."""
    case: GoldenCase = request.param
    return case
