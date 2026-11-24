"""build_registry: stage thật (hoặc noop), bọc DemoStage chỉ ở môi trường local."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import RunStatus, StageName
from citeproof.core.settings import Settings
from citeproof.db.models import VerificationRun
from citeproof.pipeline.demo import DemoStage
from citeproof.pipeline.registry import build_registry
from citeproof.pipeline.runs import create_run
from citeproof.pipeline.stages import NoopStage
from tests.db.factories import make_document
from tests.pipeline.helpers import drain, make_test_deps, pipeline_settings

SessionFactory = async_sessionmaker[AsyncSession]


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, storage_dir=tmp_path / "storage", **overrides)  # type: ignore[call-arg]


def test_build_registry_wraps_demo_only_in_local(tmp_path: Path) -> None:
    local = build_registry(make_test_deps(_settings(tmp_path, env="local"), async_sessionmaker()))
    assert isinstance(local.get(StageName.S1), DemoStage)
    for env in ("pilot", "test"):
        other = build_registry(make_test_deps(_settings(tmp_path, env=env), async_sessionmaker()))
        assert not isinstance(other.get(StageName.S1), DemoStage)
        assert isinstance(other.get(StageName.S1), NoopStage)


@pytest.mark.parametrize("env", ["local", "pilot"])
def test_build_registry_covers_every_stage(tmp_path: Path, env: str) -> None:
    registry = build_registry(make_test_deps(_settings(tmp_path, env=env), async_sessionmaker()))
    for name in StageName:
        assert registry.get(name).name is name


@pytest.mark.db
async def test_built_registry_completes_run(
    session_factory: SessionFactory, clean_db: None, tmp_path: Path
) -> None:
    async with session_factory() as session:
        doc = await make_document(session)
        run_id = await create_run(
            session, document_id=doc.id, user_id=doc.user_id, config={"variant": "V2"}
        )
        await session.commit()
    registry = build_registry(make_test_deps(_settings(tmp_path, env="pilot"), session_factory))
    assert await drain(session_factory, registry, pipeline_settings()) == len(StageName)
    async with session_factory() as session:
        status = await session.scalar(
            select(VerificationRun.status).where(VerificationRun.id == run_id)
        )
    assert status == RunStatus.COMPLETED.value
