"""S1: ghi JSON đã parse, ngôn ngữ, cảnh báo cho văn bản/DOCX/PDF; bỏ qua khi đã parse (QĐ-13)."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import Language, RunStatus, StageName
from citeproof.core.errors import InputError, LeaseLostError, RetryableError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, Job, User, VerificationRun
from citeproof.modules.documents.stage import ParseStage, merge_parse_warnings, parsed_key
from citeproof.parsing.model import ParsedDocument
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import claim_next
from citeproof.pipeline.stages import StageContext, StageRegistry, StageResult
from citeproof.services import documents
from citeproof.services.runs_view import get_run_view
from citeproof.storage import LocalStorage
from tests.files import PDF_BYTES, stamped_pdf
from tests.golden.cases import GOLDEN_DIR, GoldenCase, load_cases
from tests.pipeline.helpers import CountingStage, drain, make_test_deps, stage_context
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
async def test_s1_corrupt_pdf_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, document, ctx = await _file_document(session_factory, settings, PDF_BYTES, "bai.pdf")
    with pytest.raises(InputError) as caught:
        await ParseStage(make_test_deps(settings, session_factory)).run(ctx)
    assert caught.value.code == "UNSUPPORTED_FILE_TYPE"
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


# --- PDF (Task 6): GROBID TEI có tọa độ, dự phòng pypdfium2 (QĐ-03, QĐ-22) ---------------

_GOLDEN_PDF = [
    case for case in load_cases() if case.input_path.suffix == ".pdf" and case.expected is not None
]


class GrobidStub:
    """Handler MockTransport giả GROBID: đếm lời gọi, trả TEI/mã lỗi hoặc ném lỗi kết nối."""

    def __init__(self, *, tei: str | None = None, status: int = 200, down: bool = False) -> None:
        self.tei = tei
        self.status = status
        self.down = down
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/processFulltextDocument"
        self.calls += 1
        if self.down:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(self.status, text=self.tei or "")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _golden_pdf(name: str) -> bytes:
    return (GOLDEN_DIR / f"{name}.pdf").read_bytes()


def _recorded_tei(name: str) -> str:
    return (GOLDEN_DIR / f"{name}.fulltext.tei.xml").read_text(encoding="utf-8")


async def _queued_file_document(
    session_factory: SessionFactory, settings: Settings, data: bytes, filename: str
) -> tuple[User, Document, uuid.UUID]:
    """Tài liệu tệp và lần chạy mới; job S1 còn QUEUED (để `drain` nhận)."""
    async with session_factory() as session:
        user = await new_user(session)
        document, run_id = await documents.create_file_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            filename=filename,
            data=data,
        )
        await session.commit()
    return user, document, run_id


def _s1_registry(deps: PipelineDeps) -> StageRegistry:
    others = [CountingStage(name) for name in StageName if name is not StageName.S1]
    return StageRegistry([ParseStage(deps), *others])


async def _run(session_factory: SessionFactory, run_id: uuid.UUID) -> VerificationRun:
    async with session_factory() as session:
        return (
            await session.execute(select(VerificationRun).where(VerificationRun.id == run_id))
        ).scalar_one()


async def _s1_job(session_factory: SessionFactory, run_id: uuid.UUID) -> Job:
    async with session_factory() as session:
        return (
            await session.execute(
                select(Job).where(Job.run_id == run_id, Job.stage == StageName.S1.value)
            )
        ).scalar_one()


@pytest.mark.db
@pytest.mark.parametrize("case", _GOLDEN_PDF, ids=lambda case: case.name)
async def test_s1_golden_pdf_via_grobid(
    case: GoldenCase, session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    assert case.expected is not None and case.fulltext_tei is not None
    stub = GrobidStub(tei=case.fulltext_tei.read_text(encoding="utf-8"))
    data = case.input_path.read_bytes()
    _, document, ctx = await _file_document(session_factory, settings, data, "bai.pdf")
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    assert await ParseStage(deps).run(ctx) == StageResult()
    assert stub.calls == 1
    stored = await _document(session_factory, document.id)
    assert {w["code"] for w in stored.parse_warnings} == set(case.expected.warnings)
    assert stored.language == case.expected.language.value
    assert stored.page_count == case.expected.page_count
    assert stored.parsed_key is not None
    parsed = ParsedDocument.model_validate_json(await deps.storage.get(stored.parsed_key))
    assert parsed.source == "grobid"
    assert parsed.page_count == case.expected.page_count
    # `vi_mixed_apa`: GROBID trả listBibl rỗng, danh mục lấy từ văn bản pypdfium2.
    assert parsed.references_raw is not None
    assert len(parsed.references_raw) == len(case.expected.references)


@pytest.mark.db
async def test_s1_scanned_pages_warning(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    stub = GrobidStub(tei=_recorded_tei("vi_scanned_pages"))
    data = _golden_pdf("vi_scanned_pages")
    _, document, ctx = await _file_document(session_factory, settings, data, "scan.pdf")
    await ParseStage(make_test_deps(settings, session_factory, transport=stub.transport)).run(ctx)
    stored = await _document(session_factory, document.id)
    assert stored.parse_warnings == [{"code": "SCANNED_PAGES", "pages": [2]}]


@pytest.mark.db
async def test_page_limit_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = settings.model_copy(update={"max_pages": 2})
    stub = GrobidStub(tei=_recorded_tei("vi_scanned_pages"))
    data = _golden_pdf("vi_scanned_pages")  # 3 trang (preflight F22)
    _, document, run_id = await _queued_file_document(session_factory, settings, data, "dai.pdf")
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    with pytest.raises(InputError) as caught:
        await ParseStage(deps).run(stage_context(run_id))
    assert caught.value.code == "PAGE_LIMIT_EXCEEDED"
    assert str(caught.value) == "Tệp có 3 trang, vượt giới hạn 2 trang."
    # Qua worker: lỗi đầu vào không thử lại, lần chạy dừng ở S1.
    await drain(session_factory, _s1_registry(deps), settings)
    assert stub.calls == 0
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error == {
        "stage": "S1",
        "type": "InputError",
        "message": "InputError: Tệp có 3 trang, vượt giới hạn 2 trang.",
    }
    assert (await _s1_job(session_factory, run_id)).attempts == 1
    assert (await _document(session_factory, document.id)).parsed_key is None


@pytest.mark.db
async def test_encrypted_pdf_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    stub = GrobidStub(status=500)
    data = (GOLDEN_DIR / "encrypted.pdf").read_bytes()
    _, document, ctx = await _file_document(session_factory, settings, data, "khoa.pdf")
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    with pytest.raises(InputError) as caught:
        await ParseStage(deps).run(ctx)
    assert caught.value.code == "PDF_ENCRYPTED"
    assert str(caught.value).startswith("Tệp PDF được bảo vệ bằng mật khẩu")
    assert stub.calls == 0
    assert (await _document(session_factory, document.id)).parsed_key is None


@pytest.mark.db
async def test_image_only_pdf_fails_with_no_text_extracted(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Review Focus #1: PDF chỉ có ảnh dừng ở S1 với thông điệp tiếng Việt, không "hoàn tất" rỗng.
    stub = GrobidStub(status=500)  # GROBID thật trả 500 [NO_BLOCKS] cho tệp này (F22)
    data = (GOLDEN_DIR / "image_only.pdf").read_bytes()
    user, document, run_id = await _queued_file_document(session_factory, settings, data, "a.pdf")
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    await drain(session_factory, _s1_registry(deps), settings)
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None
    assert run.error["stage"] == "S1"
    assert run.error["type"] == "InputError"
    job = await _s1_job(session_factory, run_id)
    assert job.status == "FAILED"
    assert job.attempts == 1  # lỗi đầu vào: không thử lại
    async with session_factory() as session:
        view = await get_run_view(session, user=user, run_id=run_id)
    assert view.status is RunStatus.FAILED
    assert view.failed_stage is StageName.S1
    assert view.error_message is not None
    assert view.error_message.startswith("Không trích được văn bản")
    stored = await _document(session_factory, document.id)
    assert stored.parsed_key is None


@pytest.mark.db
async def test_all_scanned_pdf_fails_with_no_text_extracted(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Ruling: mọi trang đều là trang scan (lớp chữ chỉ có số trang/dấu ứng dụng quét) thì
    # cũng là "không trích được văn bản", không được ra báo cáo hoàn tất rỗng.
    stub = GrobidStub(status=500)
    data = stamped_pdf()
    user, document, run_id = await _queued_file_document(session_factory, settings, data, "s.pdf")
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    await drain(session_factory, _s1_registry(deps), settings)
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None
    assert run.error["stage"] == "S1"
    assert run.error["type"] == "InputError"
    async with session_factory() as session:
        view = await get_run_view(session, user=user, run_id=run_id)
    assert view.error_message == "Không trích được văn bản từ tệp PDF. Tệp có thể là ảnh quét."
    assert (await _s1_job(session_factory, run_id)).attempts == 1
    assert (await _document(session_factory, document.id)).parsed_key is None
    assert stub.calls == 0


@pytest.mark.db
async def test_grobid_down_retries_then_falls_back(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Preflight F22: backoff 0 để lần thử lại chạy ngay trong `drain`.
    settings = settings.model_copy(update={"job_max_attempts": 2, "job_backoff_base_seconds": 0.0})
    stub = GrobidStub(down=True)
    data = _golden_pdf("en_ieee")
    _, document, run_id = await _queued_file_document(session_factory, settings, data, "bai.pdf")
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    # Lần thử 1/2: GROBID không truy cập được → RetryableError, chưa ghi gì.
    with pytest.raises(RetryableError) as caught:
        await ParseStage(deps).run(stage_context(run_id, attempts=1, max_attempts=2))
    assert caught.value.code == "GROBID_UNAVAILABLE"
    assert stub.calls == 1
    assert (await _document(session_factory, document.id)).parsed_key is None
    # Worker: lượt 1 lỗi tạm thời được xếp lại, lượt 2 (lượt cuối) dùng pypdfium2.
    await drain(session_factory, _s1_registry(deps), settings)
    assert stub.calls == 3
    job = await _s1_job(session_factory, run_id)
    assert job.status == "DONE"
    assert job.attempts == 2
    stored = await _document(session_factory, document.id)
    assert stored.parse_warnings == [{"code": "PDF_TEXT_FALLBACK"}]
    assert stored.page_count == 2
    assert stored.parsed_key is not None
    parsed = ParsedDocument.model_validate_json(await deps.storage.get(stored.parsed_key))
    assert parsed.source == "pdfium"
    assert (await _run(session_factory, run_id)).status != RunStatus.FAILED.value


@pytest.mark.db
async def test_grobid_processing_error_falls_back_immediately(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    stub = GrobidStub(status=500)
    data = _golden_pdf("en_ieee")
    _, document, ctx = await _file_document(session_factory, settings, data, "bai.pdf")
    assert not ctx.is_last_attempt
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    await ParseStage(deps).run(ctx)
    assert stub.calls == 1
    stored = await _document(session_factory, document.id)
    assert stored.parse_warnings == [{"code": "PDF_TEXT_FALLBACK"}]
    assert stored.language == Language.EN.value
    assert stored.parsed_key is not None
    parsed = ParsedDocument.model_validate_json(await deps.storage.get(stored.parsed_key))
    assert parsed.source == "pdfium"


@pytest.mark.db
async def test_s1_pdf_without_grobid_uses_fallback(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    data = _golden_pdf("en_ieee")
    _, document, ctx = await _file_document(session_factory, settings, data, "bai.pdf")
    deps = make_test_deps(settings, session_factory)  # không transport: không có GROBID
    assert deps.grobid is None
    await ParseStage(deps).run(ctx)
    stored = await _document(session_factory, document.id)
    assert stored.parse_warnings == [{"code": "PDF_TEXT_FALLBACK"}]
