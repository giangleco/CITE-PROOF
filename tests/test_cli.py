import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof import cli
from citeproof.core.settings import get_settings
from citeproof.db.models import Invite, User


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("CITEPROOF_DATABASE_URL", database_url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.db
async def test_create_invite_prints_code(
    cli_env: None,
    clean_db: None,
    session_factory: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await cli.run(["create-invite", "--days", "3"]) == 0
    code = capsys.readouterr().out.strip().splitlines()[-1]
    assert len(code) >= 16
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Invite)) == 1


@pytest.mark.db
async def test_create_user_admin(
    cli_env: None,
    clean_db: None,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_read_password", lambda: "mat-khau-du-dai")
    assert await cli.run(["create-user", "--email", "Admin@b.vn", "--name", "Ad", "--admin"]) == 0
    assert "mat-khau-du-dai" not in capsys.readouterr().out
    async with session_factory() as s:
        user = (await s.execute(select(User))).scalar_one()
    assert user.email == "admin@b.vn" and user.role == "admin"


@pytest.mark.db
async def test_create_user_error_exit_code(
    cli_env: None, clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_read_password", lambda: "ngan")
    assert await cli.run(["create-user", "--email", "a@b.vn", "--name", "A"]) == 1
