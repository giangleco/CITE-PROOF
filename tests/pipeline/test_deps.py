"""PipelineDeps: dựng và đóng client dùng chung, namespace cache cho lời gọi LLM."""

import dataclasses
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import citeproof
from citeproof.core.enums import StageName
from citeproof.core.settings import Settings
from citeproof.integrations.grobid import GrobidClient
from citeproof.integrations.http_safe import SafeHttpClient
from citeproof.integrations.metadata import MetadataCache, MetadataGateway
from citeproof.llm.cache import InMemoryCacheStore, validate_namespace
from citeproof.llm.calls import InMemoryCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import CacheMode, CallContext
from citeproof.pipeline import deps as deps_module
from citeproof.pipeline.config import RunConfig, RunInfo
from citeproof.pipeline.deps import PROMPTS_ROOT, call_context, open_deps
from citeproof.storage import LocalStorage
from tests.pipeline.helpers import make_test_deps


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, storage_dir=tmp_path / "storage", **overrides)  # type: ignore[call-arg]


def _info() -> RunInfo:
    return RunInfo(
        run_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        parent_run_id=None,
        config=RunConfig(),
        document_language="vi",
    )


class ClosingProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def _patch_llm(monkeypatch: pytest.MonkeyPatch) -> ClosingProvider:
    provider = ClosingProvider()

    def fake_make_llm_client(
        settings: Settings, session_factory: object, *, mode: CacheMode = CacheMode.READ_WRITE
    ) -> LLMClient:
        return LLMClient(provider, InMemoryCacheStore(), InMemoryCallLogger())

    monkeypatch.setattr(deps_module, "make_llm_client", fake_make_llm_client)
    return provider


def test_prompts_root_is_inside_package() -> None:
    assert Path(citeproof.__file__).parent / "prompts" == PROMPTS_ROOT
    assert PROMPTS_ROOT.is_dir()


async def test_open_deps_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITEPROOF_ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    async with open_deps(settings, async_sessionmaker()) as deps:
        assert deps.llm is None
        assert deps.settings is settings
        assert isinstance(deps.storage, LocalStorage)
        assert isinstance(deps.prompts, PromptLoader)
        assert isinstance(deps.safe_http, SafeHttpClient)
        assert deps.cache_namespace is None
        http = deps.http
        assert not http.is_closed
    assert http.is_closed


async def test_open_deps_builds_llm_client_with_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path, anthropic_api_key="sk-test")
    async with open_deps(settings, async_sessionmaker()) as deps:
        assert isinstance(deps.llm, LLMClient)


async def test_open_deps_closes_llm_and_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _patch_llm(monkeypatch)
    async with open_deps(_settings(tmp_path), async_sessionmaker()) as deps:
        assert deps.llm is not None
        http = deps.http
        assert provider.closed == 0
    assert provider.closed == 1
    assert http.is_closed


async def test_open_deps_closes_clients_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _patch_llm(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"):
        async with open_deps(_settings(tmp_path), async_sessionmaker()) as deps:
            http = deps.http
            raise RuntimeError("boom")
    assert provider.closed == 1
    assert http.is_closed


def test_call_context_uses_document_namespace(tmp_path: Path) -> None:
    deps = make_test_deps(_settings(tmp_path), async_sessionmaker())
    info = _info()
    ctx = call_context(deps, info, StageName.S4)
    assert ctx == CallContext(
        namespace=f"doc:{info.document_id}",
        document_id=info.document_id,
        run_id=info.run_id,
        stage="S4",
    )
    validate_namespace(ctx.namespace, ctx.document_id)


def test_call_context_uses_eval_namespace(tmp_path: Path) -> None:
    deps = dataclasses.replace(
        make_test_deps(_settings(tmp_path), async_sessionmaker()), cache_namespace="eval:d1@abc"
    )
    info = _info()
    ctx = call_context(deps, info, StageName.S8)
    assert ctx == CallContext(
        namespace="eval:d1@abc", document_id=None, run_id=info.run_id, stage="S8"
    )
    validate_namespace(ctx.namespace, ctx.document_id)


async def test_make_test_deps_wires_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert make_test_deps(settings, async_sessionmaker()).llm is None
    deps = make_test_deps(settings, async_sessionmaker(), provider=ScriptedProvider([]))
    assert isinstance(deps.llm, LLMClient)
    await deps.http.aclose()


async def test_open_deps_builds_grobid_client(tmp_path: Path) -> None:
    settings = _settings(tmp_path, grobid_url="http://grobid:8070", grobid_timeout_seconds=12.0)
    async with open_deps(settings, async_sessionmaker()) as deps:
        assert isinstance(deps.grobid, GrobidClient)
        assert deps.grobid.base_url == "http://grobid:8070"
        assert deps.grobid.timeout_s == 12.0
    # URL rỗng: tắt GROBID, S1 dùng pypdfium2.
    async with open_deps(_settings(tmp_path, grobid_url=" "), async_sessionmaker()) as deps:
        assert deps.grobid is None


async def test_make_test_deps_grobid_only_with_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert make_test_deps(settings, async_sessionmaker()).grobid is None
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<TEI/>"))
    deps = make_test_deps(settings, async_sessionmaker(), transport=transport)
    assert deps.grobid is not None
    assert await deps.grobid.process_fulltext(b"%PDF-") == "<TEI/>"
    await deps.http.aclose()


async def test_open_deps_builds_metadata_factory(tmp_path: Path) -> None:
    async with open_deps(_settings(tmp_path), async_sessionmaker()) as deps:
        assert deps.metadata is not None
        first, second = deps.metadata(), deps.metadata()
        assert isinstance(first, MetadataGateway)
        assert first is not second  # bộ ngắt mạch theo lần chạy
        assert first.limiters is second.limiters  # giới hạn tốc độ dùng chung trong tiến trình
        assert first.sources == ("crossref", "openalex", "semantic_scholar")


async def test_make_test_deps_metadata_ttl_from_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path, metadata_cache_ttl_days=5, metadata_not_found_ttl_days=2)
    deps = make_test_deps(settings, async_sessionmaker())
    assert deps.metadata is not None
    cache = deps.metadata().cache
    assert isinstance(cache, MetadataCache)
    assert (cache.ok_ttl, cache.not_found_ttl) == (timedelta(days=5), timedelta(days=2))
    await deps.http.aclose()
