"""Phụ thuộc dùng chung của các stage và vòng đời của chúng (spec §6.3).

`open_deps` dựng một lần cho mỗi tiến trình worker và đóng mọi client khi thoát. Task
sau thêm trường (GROBID, metadata, embedder, agent) có giá trị mặc định vào cuối
`PipelineDeps`.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import citeproof
from citeproof.core.enums import StageName
from citeproof.core.settings import Settings
from citeproof.integrations.http_safe import SafeHttpClient
from citeproof.llm.cache import doc_namespace
from citeproof.llm.client import LLMClient
from citeproof.llm.factory import make_llm_client
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.types import CallContext
from citeproof.pipeline.config import RunInfo
from citeproof.storage import Storage, make_storage

PROMPTS_ROOT = Path(citeproof.__file__).parent / "prompts"
# Timeout mặc định của client dùng chung; client gọi API đặt timeout riêng khi cần.
_HTTP_TIMEOUT_S = 30.0


@dataclass
class PipelineDeps:
    session_factory: async_sessionmaker[AsyncSession]
    storage: Storage
    settings: Settings
    # None khi chưa có khóa API: các bước LLM giảm cấp tất định (`LLM_NOT_CONFIGURED`).
    llm: LLMClient | None
    prompts: PromptLoader
    # Chỉ cho host API cố định (Crossref, OpenAlex, Semantic Scholar, Unpaywall, GROBID).
    http: httpx.AsyncClient
    # Mọi URL lấy từ dữ liệu người dùng hoặc metadata bên thứ ba.
    safe_http: SafeHttpClient
    # None: cache LLM theo tài liệu (`doc:<document_id>`); đánh giá đặt `eval:<dataset>@<hash>`.
    cache_namespace: str | None = None


@asynccontextmanager
async def open_deps(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[PipelineDeps]:
    """Dựng phụ thuộc thật; đóng client LLM rồi client HTTP khi thoát (kể cả khi lỗi)."""
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S))
        llm = make_llm_client(settings, session_factory)
        if llm is not None:
            stack.push_async_callback(llm.aclose)
        yield PipelineDeps(
            session_factory=session_factory,
            storage=make_storage(settings),
            settings=settings,
            llm=llm,
            prompts=PromptLoader(PROMPTS_ROOT),
            http=http,
            safe_http=SafeHttpClient(),
        )


def call_context(deps: PipelineDeps, info: RunInfo, stage: StageName) -> CallContext:
    """Ngữ cảnh cache và ghi log cho lời gọi LLM của một stage (preflight F11).

    Sản phẩm dùng namespace của tài liệu; bộ đánh giá đặt `deps.cache_namespace` và khi
    đó entry cache không gắn với tài liệu nào.
    """
    if deps.cache_namespace is not None:
        return CallContext(
            namespace=deps.cache_namespace,
            document_id=None,
            run_id=info.run_id,
            stage=stage.value,
        )
    return CallContext(
        namespace=doc_namespace(info.document_id),
        document_id=info.document_id,
        run_id=info.run_id,
        stage=stage.value,
    )
