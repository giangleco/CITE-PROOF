"""S1 cho TEXT/DOCX: ghi JSON đã parse, ngôn ngữ, cảnh báo; bỏ qua khi đã parse (QĐ-13)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import Language, StageName
from citeproof.core.errors import LeaseLostError, ParseError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, User
from citeproof.modules.documents.stage import ParseStage, merge_parse_warnings, parsed_key
from citeproof.parsing.model import ParsedDocument
from citeproof.pipeline.jobs import claim_next
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.services import documents
from citeproof.storage import LocalStorage
from tests.files import PDF_BYTES
from tests.golden.cases import GOLDEN_DIR, GoldenCase
from tests.pipeline.helpers import make_test_deps, stage_context
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]
BODY = (
    "Mạng residual cho phép huấn luyện mạng rất sâu mà không bị suy giảm độ chính xác [1].\n\n"
    "Hệ thống hỏi đáp tiếng Việt vẫn thiếu dữ liệu huấn luyện [2]."
)
REFERENCES = (
    "[1] He K., Zhang X., Ren S., Sun J. (2016), Deep residual learning for image recognition.\n"
    "[2] Phạm Minh Đức (2018), Xây dựng hệ thống hỏi đáp tiếng Việt, Luận văn thạc sĩ."
)


class SpyStorage(LocalStorage):
    """LocalStorage ghi lại khóa của mỗi lần put/get."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.storage_dir)
        self.puts: list[str] = []
        self.gets: list[str] = []

    async def put(self, key: str, data: bytes) -> None:
        self.puts.append(key)
        await super().put(key, data)

    async def get(self, key: str) -> bytes:
        self.gets.append(key)
        return await super().get(key)


async def _claim(session_factory: SessionFactory) -> StageContext:
    async with session_factory() as session:
        lease = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        await session.commit()
    assert lease is not None and lease.stage is StageName.S1
    return StageContext(run_id=lease.run_id, lease=lease, max_attempts=3)


async def _text_document(
    session_factory: SessionFactory, settings: Settings
) -> tuple[User, Document, StageContext]:
    async with session_factory() as session:
        user = await new_user(session)
        document, _ = await documents.create_text_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            title="Bài thử",
            body=BODY,
            references=REFERENCES,
        )
        await session.commit()
    return user, document, await _claim(session_factory)


async def _file_document(
    session_factory: SessionFactory, settings: Settings, data: bytes, filename: str
) -> tuple[User, Document, StageContext]:
    async with session_factory() as session:
        user = await new_user(session)
        document, _ = await documents.create_file_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            filename=filename,
            data=data,
        )
        await session.commit()
    return user, document, await _claim(session_factory)


async def _document(session_factory: SessionFactory, document_id: uuid.UUID) -> Document:
    async with session_factory() as session:
        return (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()


async def _set(session_factory: SessionFactory, document_id: uuid.UUID, **values: Any) -> None:
    async with session_factory() as session:
        await session.execute(update(Document).where(Document.id == document_id).values(**values))
        await session.commit()


@pytest.mark.db
async def test_s1_text_document_writes_parsed_json(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    user, document, ctx = await _text_document(session_factory, settings)
    deps = make_test_deps(settings, session_factory)
    assert await ParseStage(deps).run(ctx) == StageResult()
    stored = await _document(session_factory, document.id)
    key = parsed_key(user.id, document.id)
    assert key == f"documents/{user.id}/{document.id}/parsed.json"
    assert stored.parsed_key == key
    assert stored.language == Language.VI.value
    assert stored.page_count is None
    assert stored.parse_warnings == []
    parsed = ParsedDocument.model_validate_json(await deps.storage.get(key))
    assert parsed.source == "text"
    assert parsed.language is Language.VI
    assert len(parsed.paragraphs) == 2
    assert parsed.references_raw is not None
    assert [ref.label for ref in parsed.references_raw] == ["1", "2"]


@pytest.mark.db
async def test_s1_skips_when_parsed_key_present(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, document, ctx = await _text_document(session_factory, settings)
    deps = make_test_deps(settings, session_factory)
    spy = SpyStorage(settings)
    deps.storage = spy
    stage = ParseStage(deps)
    await stage.run(ctx)
    assert len(spy.puts) == 1
    first = await _document(session_factory, document.id)
    await stage.run(ctx)
    assert len(spy.puts) == 1  # lần thứ hai không đọc lại, không ghi lại
    assert len(spy.gets) == 1
    second = await _document(session_factory, document.id)
    assert second.parsed_key == first.parsed_key


def test_merge_parse_warnings_dedupes_by_code() -> None:
    # Preflight F21: gọi trực tiếp hàm gộp hai lần (lần chạy S1 thứ hai bị bỏ qua).
    existing: list[Any] = [{"code": "SAMPLE_DOCUMENT"}]
    legacy = [{"code": "LEGACY_ENCODING", "encoding": "TCVN3"}]
    once = merge_parse_warnings(existing, legacy)
    twice = merge_parse_warnings(once, [{"code": "LEGACY_ENCODING", "encoding": "VNI"}])
    assert twice == [{"code": "SAMPLE_DOCUMENT"}, {"code": "LEGACY_ENCODING", "encoding": "TCVN3"}]
    assert existing == [{"code": "SAMPLE_DOCUMENT"}]  # không sửa danh sách đầu vào
    # Mục lạ (không phải dict có `code`) được giữ nguyên, không làm hỏng việc gộp.
    assert merge_parse_warnings(["x", {"y": 1}], legacy) == ["x", {"y": 1}, *legacy]


@pytest.mark.db
async def test_s1_keeps_sample_marker_and_dedupes(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    data = (GOLDEN_DIR / "vi_tcvn3_legacy.docx").read_bytes()
    _, document, ctx = await _file_document(session_factory, settings, data, "cu.docx")
    await _set(session_factory, document.id, parse_warnings=[{"code": "SAMPLE_DOCUMENT"}])
    stage = ParseStage(make_test_deps(settings, session_factory))
    await stage.run(ctx)
    # Xóa dấu "đã parse" để lần chạy thứ hai thực sự gộp cảnh báo lần nữa.
    await _set(session_factory, document.id, parsed_key=None)
    await stage.run(ctx)
    stored = await _document(session_factory, document.id)
    assert stored.parse_warnings == [
        {"code": "SAMPLE_DOCUMENT"},
        {"code": "LEGACY_ENCODING", "encoding": "TCVN3"},
    ]
    assert stored.language == Language.UNKNOWN.value


@pytest.mark.db
async def test_s1_golden_docx(
    golden_docx_case: GoldenCase,
    session_factory: SessionFactory,
    clean_db: None,
    settings: Settings,
) -> None:
    assert golden_docx_case.expected is not None
    data = golden_docx_case.input_path.read_bytes()
    _, document, ctx = await _file_document(session_factory, settings, data, "bai.docx")
    deps = make_test_deps(settings, session_factory)
    await ParseStage(deps).run(ctx)
    stored = await _document(session_factory, document.id)
    # Preflight F20: so cảnh báo theo tập mã.
    assert {w["code"] for w in stored.parse_warnings} == set(golden_docx_case.expected.warnings)
    assert stored.language == golden_docx_case.expected.language.value
    assert stored.page_count is None
    assert stored.parsed_key is not None
    parsed = ParsedDocument.model_validate_json(await deps.storage.get(stored.parsed_key))
    assert parsed.source == "docx"


@pytest.mark.db
async def test_s1_pdf_waits_for_task_6(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, document, ctx = await _file_document(session_factory, settings, PDF_BYTES, "bai.pdf")
    with pytest.raises(ParseError):
        await ParseStage(make_test_deps(settings, session_factory)).run(ctx)
    assert (await _document(session_factory, document.id)).parsed_key is None


@pytest.mark.db
async def test_s1_lost_lease_writes_nothing(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, document, ctx = await _text_document(session_factory, settings)
    # Lease không khớp dòng job nào (đã bị worker khác nhận lại): không được ghi kết quả.
    with pytest.raises(LeaseLostError):
        await ParseStage(make_test_deps(settings, session_factory)).run(stage_context(ctx.run_id))
    stored = await _document(session_factory, document.id)
    assert stored.parsed_key is None
    assert stored.language == Language.UNKNOWN.value
