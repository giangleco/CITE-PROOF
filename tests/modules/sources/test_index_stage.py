"""S6 — parse nguồn, chia đoạn và lập chỉ mục (spec §7.6, §6.4; T-405, ADR-007).

Nguồn được tạo thẳng trong DB như S5 (bản mở dùng chung) hoặc như tab tải lên (`USER_UPLOAD`)
sẽ ghi. GROBID giả bằng `httpx.MockTransport` trả TEI đã ghi (`tests/fixtures/golden`); không
truyền transport thì `deps.grobid` là None và PDF parse bằng pypdfium2. Các stage khác là
`CountingStage`.
"""

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    AccessStatus,
    ChunkRegion,
    ContentLevel,
    RunStatus,
    SourceOrigin,
    StageName,
    TitleMatch,
)
from citeproof.core.errors import RetryableError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Job,
    ReferenceEntry,
    SourceChunk,
    SourceFile,
    User,
    VerificationRun,
)
from citeproof.db.models.sources import EMBEDDING_DIM
from citeproof.modules.retrieval.embedding import Embedder, NoEmbedder
from citeproof.modules.sources.parse_source import parse_source, parsed_source_key
from citeproof.modules.sources.selection import relevant_source_files
from citeproof.modules.sources.stage_index import IndexSourcesStage
from citeproof.parsing.model import ParsedDocument
from citeproof.pipeline.config import load_run_info
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.stages import StageRegistry
from citeproof.storage import LocalStorage
from tests.golden.cases import GOLDEN_DIR
from tests.modules.documents.test_parse_stage import GrobidStub
from tests.modules.sources.test_fetch_stage import _cite, _work
from tests.pipeline.helpers import CountingStage, drain, make_test_deps, pipeline_settings

SessionFactory = async_sessionmaker[AsyncSession]

EN_IEEE_PDF = (GOLDEN_DIR / "en_ieee.pdf").read_bytes()  # 2 trang, có TEI đã ghi
EN_IEEE_TEI = (GOLDEN_DIR / "en_ieee.fulltext.tei.xml").read_text(encoding="utf-8")
ABSTRACT_TEXT = (
    "We study how sequence models can be used to check that citations in a thesis match the "
    "cited sources. Experiments compare a recurrent baseline with a fine-tuned encoder."
)
TEXT_SOURCE = (
    "Mô hình mã hóa được tinh chỉnh trên dữ liệu đích.\n\n"
    "Kết nối residual giúp huấn luyện ổn định các mô hình sâu.\r\n  \r\n"
    "Độ chính xác đạt 95.7% trên tập kiểm tra."
)


def _abstract_json(title: str = "Sequence Models", abstract: str = ABSTRACT_TEXT) -> bytes:
    return json.dumps({"title": title, "abstract": abstract}, ensure_ascii=False).encode()


async def _store(settings: Settings, key: str, data: bytes) -> None:
    await LocalStorage(settings.storage_dir).put(key, data)


async def _open_source(
    session_factory: SessionFactory,
    settings: Settings,
    work_id: uuid.UUID,
    data: bytes,
    ext: str,
    level: ContentLevel = ContentLevel.FULL_TEXT,
) -> uuid.UUID:
    """Bản mở dùng chung như S5 ghi: `sources/open/<sha>.<ext>`, không chủ sở hữu."""
    sha = hashlib.sha256(data).hexdigest()
    key = f"sources/open/{sha}.{ext}"
    await _store(settings, key, data)
    status = (
        AccessStatus.OPEN_ABSTRACT
        if level is ContentLevel.ABSTRACT
        else AccessStatus.OPEN_FULL_TEXT
    )
    async with session_factory() as s:
        source = SourceFile(
            work_id=work_id,
            origin=SourceOrigin.OPEN_ACCESS.value,
            access_status=status.value,
            content_level=level.value,
            storage_key=key,
            sha256=sha,
            title_match=TitleMatch.UNKNOWN.value,
        )
        s.add(source)
        await s.commit()
        return source.id


async def _upload(
    session_factory: SessionFactory,
    settings: Settings,
    *,
    owner: User,
    reference_entry_id: uuid.UUID,
    work_id: uuid.UUID | None,
    text: str = TEXT_SOURCE,
) -> uuid.UUID:
    """Tệp người dùng tải lên cho một tài liệu tham khảo (lưu dưới tiền tố của chủ sở hữu)."""
    async with session_factory() as s:
        document_id = await s.scalar(
            select(ReferenceEntry.document_id).where(ReferenceEntry.id == reference_entry_id)
        )
    data = text.encode()
    key = f"documents/{owner.id}/{document_id}/sources/{uuid.uuid4().hex}.txt"
    await _store(settings, key, data)
    async with session_factory() as s:
        source = SourceFile(
            work_id=work_id,
            reference_entry_id=reference_entry_id,
            owner_user_id=owner.id,
            origin=SourceOrigin.USER_UPLOAD.value,
            access_status=AccessStatus.USER_PROVIDED.value,
            content_level=ContentLevel.FULL_TEXT.value,
            storage_key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            title_match=TitleMatch.UNKNOWN.value,
        )
        s.add(source)
        await s.commit()
        return source.id


async def _entries(session_factory: SessionFactory, run_id: uuid.UUID) -> list[uuid.UUID]:
    async with session_factory() as s:
        document_id = await s.scalar(
            select(VerificationRun.document_id).where(VerificationRun.id == run_id)
        )
        return list(
            (
                await s.execute(
                    select(ReferenceEntry.id)
                    .where(ReferenceEntry.document_id == document_id)
                    .order_by(ReferenceEntry.order_index)
                )
            ).scalars()
        )


def _registry(stage: IndexSourcesStage) -> StageRegistry:
    return StageRegistry(
        stage if name is StageName.S6 else CountingStage(name) for name in StageName
    )


async def _run_s6(session_factory: SessionFactory, deps: PipelineDeps) -> None:
    await drain(session_factory, _registry(IndexSourcesStage(deps)), pipeline_settings())


async def _chunks(
    session_factory: SessionFactory, source_file_id: uuid.UUID | None = None
) -> list[SourceChunk]:
    query = select(SourceChunk).order_by(SourceChunk.source_file_id, SourceChunk.chunk_index)
    if source_file_id is not None:
        query = query.where(SourceChunk.source_file_id == source_file_id)
    async with session_factory() as s:
        return list((await s.execute(query)).scalars())


async def _run_status(session_factory: SessionFactory, run_id: uuid.UUID) -> str:
    async with session_factory() as s:
        status = await s.scalar(select(VerificationRun.status).where(VerificationRun.id == run_id))
    assert status is not None
    return status


async def _source(session_factory: SessionFactory, source_id: uuid.UUID) -> SourceFile:
    async with session_factory() as s:
        source = await s.get(SourceFile, source_id)
    assert source is not None
    return source


# --- parse_source -----------------------------------------------------------------------------


def _source_file(key: str) -> SourceFile:
    return SourceFile(storage_key=key)


def _deps(settings: Settings) -> PipelineDeps:
    return make_test_deps(settings, async_sessionmaker())


def test_parsed_source_key() -> None:
    assert parsed_source_key("sources/open/ab.pdf") == "sources/open/ab.pdf.parsed.json"


async def test_parse_source_abstract_json(settings: Settings) -> None:
    await _store(settings, "sources/open/a.abstract.json", _abstract_json())
    doc = await parse_source(
        _deps(settings), _source_file("sources/open/a.abstract.json"), allow_fallback=False
    )
    assert doc is not None
    [paragraph] = doc.paragraphs
    assert (paragraph.text, paragraph.section, paragraph.page) == (ABSTRACT_TEXT, "Abstract", None)
    assert doc.source == "text" and doc.page_count is None


async def test_parse_source_text_file(settings: Settings) -> None:
    # Nguồn của bộ đánh giá là văn bản thuần `<sha>.txt` (preflight F10).
    await _store(settings, "sources/open/t.txt", TEXT_SOURCE.encode())
    doc = await parse_source(
        _deps(settings), _source_file("sources/open/t.txt"), allow_fallback=False
    )
    assert doc is not None
    assert doc.source == "text"
    assert [p.text for p in doc.paragraphs] == [
        "Mô hình mã hóa được tinh chỉnh trên dữ liệu đích.",
        "Kết nối residual giúp huấn luyện ổn định các mô hình sâu.",
        "Độ chính xác đạt 95.7% trên tập kiểm tra.",
    ]


@pytest.mark.parametrize(
    ("key", "data"),
    [
        ("sources/open/x.abstract.json", b"{not json"),
        ("sources/open/x.abstract.json", json.dumps({"title": "T", "abstract": "  "}).encode()),
        ("sources/open/x.abstract.json", json.dumps(["abstract"]).encode()),
        ("sources/open/x.txt", b"\xff\xfe\x00bad"),
        ("sources/open/x.txt", b" \n\n \n"),
        ("sources/open/x.docx", b"PK\x03\x04"),
        ("sources/open/x.pdf", b"%PDF-1.7 broken"),
        ("sources/open/x.pdf", (GOLDEN_DIR / "encrypted.pdf").read_bytes()),
        ("sources/open/x.pdf", (GOLDEN_DIR / "image_only.pdf").read_bytes()),
    ],
)
async def test_parse_source_unreadable_returns_none(
    settings: Settings, key: str, data: bytes
) -> None:
    # Preflight F09: lỗi đầu vào/parse của một nguồn → None (S8 ra SOURCE_PARSE_FAILED).
    await _store(settings, key, data)
    assert await parse_source(_deps(settings), _source_file(key), allow_fallback=True) is None


async def test_parse_source_page_limit(settings: Settings) -> None:
    await _store(settings, "sources/open/p.pdf", EN_IEEE_PDF)
    stub = GrobidStub(tei=EN_IEEE_TEI)
    limited = settings.model_copy(update={"max_source_pages": 1})
    deps = make_test_deps(limited, async_sessionmaker(), transport=stub.transport)
    assert await parse_source(deps, _source_file("sources/open/p.pdf"), allow_fallback=True) is None
    assert stub.calls == 0  # từ chối trước khi gọi GROBID


async def test_parse_source_pdf_via_grobid(settings: Settings) -> None:
    await _store(settings, "sources/open/p.pdf", EN_IEEE_PDF)
    stub = GrobidStub(tei=EN_IEEE_TEI)
    deps = make_test_deps(settings, async_sessionmaker(), transport=stub.transport)
    doc = await parse_source(deps, _source_file("sources/open/p.pdf"), allow_fallback=False)
    assert doc is not None and doc.source == "grobid"
    assert doc.page_count == 2
    assert doc.paragraphs[0].section == "Abstract"
    assert stub.calls == 1


async def test_parse_source_grobid_down_retries_unless_fallback(settings: Settings) -> None:
    await _store(settings, "sources/open/p.pdf", EN_IEEE_PDF)
    stub = GrobidStub(down=True)
    deps = make_test_deps(settings, async_sessionmaker(), transport=stub.transport)
    source = _source_file("sources/open/p.pdf")
    with pytest.raises(RetryableError):
        await parse_source(deps, source, allow_fallback=False)
    doc = await parse_source(deps, source, allow_fallback=True)  # lần thử cuối (QĐ-22)
    assert doc is not None and doc.source == "pdfium"


# --- Embedder ---------------------------------------------------------------------------------


async def test_no_embedder() -> None:
    embedder: Embedder = NoEmbedder()
    assert embedder.model is None
    assert await embedder.embed(["một đoạn"]) is None


@dataclasses.dataclass
class FakeEmbedder:
    model: str | None = "fake-embed-1"
    calls: list[list[str]] = dataclasses.field(default_factory=list)
    before_return: Callable[[], Awaitable[None]] | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        self.calls.append(list(texts))
        if self.before_return is not None:
            await self.before_return()
        return [[float(i % 7) / 10] * EMBEDDING_DIM for i, _ in enumerate(texts)]


# --- S6 trên DB -------------------------------------------------------------------------------


@pytest.mark.db
async def test_s6_writes_chunks_and_tsv(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    work_id = await _work(session_factory, "Sequence Models for Citation Checking")
    _, run_id = await _cite(session_factory, [work_id])
    full_id = await _open_source(session_factory, settings, work_id, EN_IEEE_PDF, "pdf")
    abstract_id = await _open_source(
        session_factory,
        settings,
        work_id,
        _abstract_json(),
        "abstract.json",
        ContentLevel.ABSTRACT,
    )
    stub = GrobidStub(tei=EN_IEEE_TEI)
    await _run_s6(
        session_factory, make_test_deps(settings, session_factory, transport=stub.transport)
    )

    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value
    assert stub.calls == 1
    storage = LocalStorage(settings.storage_dir)
    for source_id in (full_id, abstract_id):
        source = await _source(session_factory, source_id)
        parsed = ParsedDocument.model_validate_json(
            await storage.get(parsed_source_key(source.storage_key))
        )
        chunks = await _chunks(session_factory, source_id)
        assert chunks
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        for chunk in chunks:
            assert parsed.text()[chunk.char_start : chunk.char_end] == chunk.text
            assert chunk.embedding is None and chunk.embedding_model is None
            assert chunk.language == "en"

    full = await _chunks(session_factory, full_id)
    assert full[0].section == "Abstract" and full[0].page == 1
    [table] = [c for c in full if c.region == ChunkRegion.TABLE.value]
    assert table.section == "III. Results" and table.page == 2
    [abstract] = await _chunks(session_factory, abstract_id)
    assert (abstract.text, abstract.section) == (ABSTRACT_TEXT, "Abstract")

    async with session_factory() as s:
        missing_tsv = await s.scalar(
            select(func.count()).select_from(SourceChunk).where(SourceChunk.tsv.is_(None))
        )
        hits = (
            await s.execute(
                select(SourceChunk.source_file_id).where(
                    SourceChunk.tsv.op("@@")(func.to_tsquery("simple", "encoder"))
                )
            )
        ).scalars()
        assert missing_tsv == 0
        assert set(hits) == {full_id, abstract_id}


@pytest.mark.db
async def test_s6_grobid_down_retries_then_falls_back(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Như S1 (QĐ-22): GROBID không truy cập được → thử lại; lượt cuối dùng pypdfium2.
    settings = settings.model_copy(update={"job_max_attempts": 2, "job_backoff_base_seconds": 0.0})
    work_id = await _work(session_factory, "Sequence Models for Citation Checking")
    _, run_id = await _cite(session_factory, [work_id])
    source_id = await _open_source(session_factory, settings, work_id, EN_IEEE_PDF, "pdf")
    stub = GrobidStub(down=True)
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    await drain(session_factory, _registry(IndexSourcesStage(deps)), settings)

    assert stub.calls == 2
    async with session_factory() as s:
        job = (
            await s.execute(select(Job).where(Job.run_id == run_id, Job.stage == StageName.S6))
        ).scalar_one()
    assert (job.status, job.attempts) == ("DONE", 2)
    source = await _source(session_factory, source_id)
    parsed = ParsedDocument.model_validate_json(
        await LocalStorage(settings.storage_dir).get(parsed_source_key(source.storage_key))
    )
    assert parsed.source == "pdfium"
    chunks = await _chunks(session_factory, source_id)
    assert chunks
    for chunk in chunks:
        assert parsed.text()[chunk.char_start : chunk.char_end] == chunk.text


@pytest.mark.db
async def test_s6_idempotent(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    work_id = await _work(session_factory, "Sequence Models for Citation Checking")
    await _cite(session_factory, [work_id])
    source_id = await _open_source(session_factory, settings, work_id, EN_IEEE_PDF, "pdf")
    stub = GrobidStub(tei=EN_IEEE_TEI)
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    await _run_s6(session_factory, deps)
    first = [(c.id, c.chunk_index, c.text) for c in await _chunks(session_factory)]

    # Lần chạy khác (người dùng khác) cùng trích work: dùng lại chunk, không parse lại.
    _, second_run = await _cite(session_factory, [work_id])
    await _run_s6(session_factory, deps)
    assert [(c.id, c.chunk_index, c.text) for c in await _chunks(session_factory)] == first
    assert stub.calls == 1
    assert await _run_status(session_factory, second_run) == RunStatus.COMPLETED.value
    assert {c.source_file_id for c in await _chunks(session_factory)} == {source_id}


@pytest.mark.db
async def test_s6_concurrent_writer_does_not_duplicate(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    """Lần chạy khác ghi chunk cho cùng tệp giữa lúc kiểm tra và lúc ghi: không ghi trùng."""
    work_id = await _work(session_factory, "Shared source")
    await _cite(session_factory, [work_id])
    source_id = await _open_source(session_factory, settings, work_id, TEXT_SOURCE.encode(), "txt")

    async def other_run_wins() -> None:
        async with session_factory() as s, s.begin():
            await s.execute(
                insert(SourceChunk).values(
                    source_file_id=source_id,
                    chunk_index=0,
                    text="Mô hình mã hóa được tinh chỉnh trên dữ liệu đích.",
                    page=None,
                    section=None,
                    char_start=0,
                    char_end=49,
                    region=ChunkRegion.BODY.value,
                    language="vi",
                )
            )

    embedder = FakeEmbedder(before_return=other_run_wins)
    deps = dataclasses.replace(make_test_deps(settings, session_factory), embedder=embedder)
    await _run_s6(session_factory, deps)

    assert len(embedder.calls) == 1
    [chunk] = await _chunks(session_factory)
    assert chunk.embedding is None  # dòng của lần chạy thắng giữ nguyên


@pytest.mark.db
async def test_s6_stores_embeddings_when_embedder_returns_vectors(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    work_id = await _work(session_factory, "Embedded source")
    await _cite(session_factory, [work_id])
    await _open_source(session_factory, settings, work_id, TEXT_SOURCE.encode(), "txt")
    embedder = FakeEmbedder()
    deps = dataclasses.replace(make_test_deps(settings, session_factory), embedder=embedder)
    await _run_s6(session_factory, deps)

    chunks = await _chunks(session_factory)
    assert chunks
    assert embedder.calls == [[c.text for c in chunks]]
    for index, chunk in enumerate(chunks):
        assert chunk.embedding_model == "fake-embed-1"
        assert chunk.embedding is not None and len(chunk.embedding) == EMBEDDING_DIM
        assert chunk.embedding[0] == pytest.approx(float(index % 7) / 10)


@pytest.mark.db
async def test_other_users_upload_not_indexed_for_run(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    work_id = await _work(session_factory, "Shared work")
    user_a, run_a = await _cite(session_factory, [work_id])
    user_b, run_b = await _cite(session_factory, [work_id])
    _, other_doc_run = await _cite(session_factory, [work_id], user=user_a)
    [entry_a] = await _entries(session_factory, run_a)
    [entry_b] = await _entries(session_factory, run_b)
    [entry_a_other_doc] = await _entries(session_factory, other_doc_run)

    open_id = await _open_source(session_factory, settings, work_id, TEXT_SOURCE.encode(), "txt")
    own = await _upload(
        session_factory, settings, owner=user_a, reference_entry_id=entry_a, work_id=work_id
    )
    foreign = await _upload(
        session_factory, settings, owner=user_b, reference_entry_id=entry_b, work_id=work_id
    )
    other_doc = await _upload(
        session_factory,
        settings,
        owner=user_a,
        reference_entry_id=entry_a_other_doc,
        work_id=work_id,
    )

    async with session_factory() as s:
        selected = await relevant_source_files(s, await load_run_info(s, run_a))
        selected_b = await relevant_source_files(s, await load_run_info(s, run_b))
    assert [f.id for f in selected] == [open_id, own]
    assert [f.id for f in selected_b] == [open_id, foreign]

    # Chỉ lần chạy của A được xử lý: tệp của B và tệp cho tài liệu khác của A không được đụng tới.
    async with session_factory() as s, s.begin():
        await s.execute(delete(Job).where(Job.run_id.in_([run_b, other_doc_run])))
    await _run_s6(session_factory, make_test_deps(settings, session_factory))
    assert {c.source_file_id for c in await _chunks(session_factory)} == {open_id, own}
    assert foreign != other_doc and user_b.id != user_a.id


@pytest.mark.db
async def test_upload_outside_scope_not_selected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    first = await _work(session_factory, "First")
    second = await _work(session_factory, "Second")
    user, run_id = await _cite(session_factory, [first, second], scope=[1])
    entries = await _entries(session_factory, run_id)
    first_open = await _open_source(session_factory, settings, first, b"First source.", "txt")
    second_open = await _open_source(session_factory, settings, second, b"Second source.", "txt")
    out_of_scope = await _upload(
        session_factory, settings, owner=user, reference_entry_id=entries[0], work_id=first
    )
    in_scope = await _upload(
        session_factory, settings, owner=user, reference_entry_id=entries[1], work_id=second
    )

    async with session_factory() as s:
        selected = await relevant_source_files(s, await load_run_info(s, run_id))
    assert [f.id for f in selected] == [second_open, in_scope]
    assert first_open not in {f.id for f in selected}
    assert out_of_scope not in {f.id for f in selected}


@pytest.mark.db
async def test_unparseable_source_yields_no_chunks(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    scanned_work = await _work(session_factory, "Scanned")
    locked_work = await _work(session_factory, "Locked")
    good_work = await _work(session_factory, "Good")
    _, run_id = await _cite(session_factory, [scanned_work, locked_work, good_work])
    scanned = await _open_source(
        session_factory, settings, scanned_work, (GOLDEN_DIR / "image_only.pdf").read_bytes(), "pdf"
    )
    locked = await _open_source(
        session_factory, settings, locked_work, (GOLDEN_DIR / "encrypted.pdf").read_bytes(), "pdf"
    )
    good = await _open_source(session_factory, settings, good_work, TEXT_SOURCE.encode(), "txt")
    await _run_s6(session_factory, make_test_deps(settings, session_factory))

    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value
    assert {c.source_file_id for c in await _chunks(session_factory)} == {good}
    storage = LocalStorage(settings.storage_dir)
    for source_id in (scanned, locked):
        source = await _source(session_factory, source_id)
        with pytest.raises(FileNotFoundError):
            await storage.get(parsed_source_key(source.storage_key))
