"""build_registry: stage thật (hoặc noop), bọc DemoStage chỉ ở môi trường local."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import RunStatus, StageName
from citeproof.core.settings import Settings
from citeproof.db.models import Document, VerificationRun
from citeproof.modules.citations.stage import CitationsStage
from citeproof.modules.documents.stage import ParseStage
from citeproof.modules.references.stage_check import CheckReferencesStage
from citeproof.modules.references.stage_extract import ExtractReferencesStage
from citeproof.pipeline.demo import DemoStage
from citeproof.pipeline.registry import build_registry
from citeproof.pipeline.stages import NoopStage
from citeproof.services import documents
from citeproof.storage import LocalStorage
from tests.pipeline.helpers import drain, make_test_deps, metadata_not_found, pipeline_settings
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, storage_dir=tmp_path / "storage", **overrides)  # type: ignore[call-arg]


def test_build_registry_wraps_demo_only_in_local(tmp_path: Path) -> None:
    local = build_registry(make_test_deps(_settings(tmp_path, env="local"), async_sessionmaker()))
    wrapped = local.get(StageName.S1)
    assert isinstance(wrapped, DemoStage)
    assert isinstance(wrapped.inner, ParseStage)
    for env in ("pilot", "test"):
        other = build_registry(make_test_deps(_settings(tmp_path, env=env), async_sessionmaker()))
        assert not isinstance(other.get(StageName.S1), DemoStage)
        assert isinstance(other.get(StageName.S1), ParseStage)
        assert isinstance(other.get(StageName.S2), ExtractReferencesStage)
        assert isinstance(other.get(StageName.S3), CheckReferencesStage)
        assert isinstance(other.get(StageName.S4), CitationsStage)
        assert isinstance(other.get(StageName.S5), NoopStage)


@pytest.mark.parametrize("env", ["local", "pilot"])
def test_build_registry_covers_every_stage(tmp_path: Path, env: str) -> None:
    registry = build_registry(make_test_deps(_settings(tmp_path, env=env), async_sessionmaker()))
    for name in StageName:
        assert registry.get(name).name is name


@pytest.mark.db
async def test_built_registry_completes_run(
    session_factory: SessionFactory, clean_db: None, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, env="pilot", grobid_url="")
    async with session_factory() as session:
        user = await new_user(session)
        doc, run_id = await documents.create_text_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            title=None,
            body="Mô hình đạt độ chính xác cao hơn [1].",
            references="[1] Nguyễn Văn An (2020), Phân loại văn bản tiếng Việt.",
        )
        await session.commit()
    transport = httpx.MockTransport(metadata_not_found)  # S3 thật: không gọi API thật
    registry = build_registry(make_test_deps(settings, session_factory, transport=transport))
    assert await drain(session_factory, registry, pipeline_settings()) == len(StageName)
    async with session_factory() as session:
        status = await session.scalar(
            select(VerificationRun.status).where(VerificationRun.id == run_id)
        )
        parsed_key = await session.scalar(select(Document.parsed_key).where(Document.id == doc.id))
    assert status == RunStatus.COMPLETED_WITH_WARNINGS.value  # S4: LLM_NOT_CONFIGURED
    assert parsed_key is not None
