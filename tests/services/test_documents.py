import asyncio
import json
import unicodedata

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, RunStatus, StageName, UserRole, Verifier
from citeproof.core.errors import ConflictError, InputError, RateLimitedError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, Job, User, VerificationRun
from citeproof.services import auth, documents
from citeproof.services.documents import DemoConfig
from citeproof.storage import LocalStorage
from tests.files import PDF_BYTES, docx_bytes

SessionFactory = async_sessionmaker[AsyncSession]


def test_sniff_pdf_and_docx() -> None:
    assert documents.sniff_kind(PDF_BYTES) == "pdf"
    assert documents.sniff_kind(docx_bytes()) == "docx"
    assert documents.sniff_kind(docx_bytes(with_document=False)) is None
    assert documents.sniff_kind(b"GIF89a....") is None
    assert documents.sniff_kind(b"PK\x03\x04 broken") is None


def test_clean_filename() -> None:
    assert documents.clean_filename("../../etc/passwd") == "passwd"
    assert documents.clean_filename("C:\\Users\\a\\bai.pdf") == "bai.pdf"
    nfd = unicodedata.normalize("NFD", "Khóa luận.pdf")
    assert documents.clean_filename(nfd) == unicodedata.normalize("NFC", "Khóa luận.pdf")
    assert documents.clean_filename("\x00a\x1fb.pdf") == "ab.pdf"
    assert len(documents.clean_filename("a" * 300 + ".pdf")) == 200
    assert documents.clean_filename(None) == "tai-lieu"
    assert documents.clean_filename("   ") == "tai-lieu"


async def _user(s: AsyncSession, role: UserRole = UserRole.USER, email: str = "u@b.vn") -> User:
    user = await auth.create_user(
        s, email=email, password="mat-khau-du-dai", display_name="U", role=role
    )
    await s.commit()
    return user


@pytest.mark.db
async def test_create_file_document_stores_and_enqueues(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await _user(s)
        doc, run_id = await documents.create_file_document(
            s, storage, settings, user=user, filename="bai.pdf", data=PDF_BYTES
        )
        await s.commit()
        assert doc.kind == "FILE" and doc.mime_type == "application/pdf"
        assert await storage.get(doc.storage_key) == PDF_BYTES
        assert doc.storage_key.endswith("/original.pdf")
        run = await s.get(VerificationRun, run_id)
        assert run is not None and run.status == RunStatus.QUEUED.value
        assert run.config == {"variant": "V2"}
        stages = (await s.execute(select(Job.stage).where(Job.run_id == run_id))).scalars().all()
        assert stages == [StageName.S1.value]


@pytest.mark.db
async def test_rejects_disguised_file(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await _user(s)
        with pytest.raises(InputError) as exc:
            await documents.create_file_document(
                s, storage, settings, user=user, filename="a.pdf", data=b"MZ\x90\x00"
            )
        assert exc.value.code == "UNSUPPORTED_FILE_TYPE"
        assert await s.scalar(select(func.count()).select_from(Document)) == 0
    assert not settings.storage_dir.exists() or not any(settings.storage_dir.rglob("*.*"))


@pytest.mark.db
async def test_text_document_nfc_and_limits(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    body = unicodedata.normalize("NFD", "Kiến trúc Transformer [1].")
    async with session_factory() as s:
        user = await _user(s)
        doc, _ = await documents.create_text_document(
            s, storage, settings, user=user, title=None, body=body, references="[1] Vaswani 2017"
        )
        await s.commit()
        stored = json.loads(await storage.get(doc.storage_key))
        assert stored["body"] == unicodedata.normalize("NFC", body)
        assert doc.kind == "TEXT" and doc.filename.startswith("Đoạn văn ")
        small = settings.model_copy(update={"max_text_chars": 5})
        with pytest.raises(InputError) as exc:
            await documents.create_text_document(
                s, storage, small, user=user, title="x", body="dài hơn năm", references="[1]"
            )
        assert exc.value.code == "TEXT_TOO_LONG"
        with pytest.raises(InputError) as empty:
            await documents.create_text_document(
                s, storage, settings, user=user, title="x", body="  ", references="[1]"
            )
        assert empty.value.code == "EMPTY_TEXT"


@pytest.mark.db
async def test_one_active_run_concurrent(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await _user(s)
        doc, run_id = await documents.create_file_document(
            s, storage, settings, user=user, filename="a.pdf", data=PDF_BYTES
        )
        run = await s.get(VerificationRun, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED.value
        await s.commit()
        doc_id, user_id = doc.id, user.id

    async def attempt() -> str:
        async with session_factory() as s:
            u = await s.get(User, user_id)
            d = await s.get(Document, doc_id)
            assert u is not None and d is not None
            try:
                await documents.start_run(s, settings, user=u, document=d)
                await asyncio.sleep(0.2)
                await s.commit()
                return "ok"
            except ConflictError as exc:
                assert exc.code == "RUN_IN_PROGRESS"
                return "conflict"

    results = sorted(await asyncio.gather(attempt(), attempt()))
    assert results == ["conflict", "ok"]


@pytest.mark.db
async def test_quota(session_factory: SessionFactory, clean_db: None, settings: Settings) -> None:
    storage = LocalStorage(settings.storage_dir)
    tight = settings.model_copy(update={"runs_per_day": 2})
    async with session_factory() as s:
        user = await _user(s)
        admin = await _user(s, UserRole.ADMIN, "ad@b.vn")
        for owner in (user, user):
            _, _run = await documents.create_file_document(
                s, storage, tight, user=owner, filename="a.pdf", data=PDF_BYTES
            )
        await s.commit()
        with pytest.raises(RateLimitedError) as exc:
            await documents.create_file_document(
                s, storage, tight, user=user, filename="a.pdf", data=PDF_BYTES
            )
        assert exc.value.code == "QUOTA_EXCEEDED"
        await s.rollback()
        await s.refresh(admin)
        for _ in range(3):
            await documents.create_file_document(
                s, storage, tight, user=admin, filename="a.pdf", data=PDF_BYTES
            )


@pytest.mark.db
async def test_variant_forced_for_non_admin(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await _user(s)
        admin = await _user(s, UserRole.ADMIN, "ad@b.vn")
        _, r1 = await documents.create_file_document(
            s, storage, settings, user=user, filename="a.pdf", data=PDF_BYTES, variant=Verifier.V0
        )
        _, r2 = await documents.create_file_document(
            s, storage, settings, user=admin, filename="a.pdf", data=PDF_BYTES, variant=Verifier.V0
        )
        runs = {r.id: r.config for r in (await s.execute(select(VerificationRun))).scalars()}
    assert runs[r1]["variant"] == "V2"
    assert runs[r2]["variant"] == "V0"


@pytest.mark.db
async def test_demo_kept_only_in_local(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    demo = DemoConfig(step_delay_s=1.5, fail_at=StageName.S5)
    pilot = settings.model_copy(update={"env": "pilot"})
    async with session_factory() as s:
        user = await _user(s)
        _, local_run = await documents.create_file_document(
            s, storage, settings, user=user, filename="a.pdf", data=PDF_BYTES, demo=demo
        )
        _, pilot_run = await documents.create_file_document(
            s, storage, pilot, user=user, filename="a.pdf", data=PDF_BYTES, demo=demo
        )
        configs = {r.id: r.config for r in (await s.execute(select(VerificationRun))).scalars()}
    assert configs[local_run]["demo"] == {"step_delay_s": 1.5, "fail_at": "S5"}
    assert "demo" not in configs[pilot_run]


@pytest.mark.db
async def test_list_documents_latest_run(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await _user(s)
        other = await _user(s, email="o@b.vn")
        _doc, run_id = await documents.create_file_document(
            s, storage, settings, user=user, filename="bai.pdf", data=PDF_BYTES
        )
        await documents.create_file_document(
            s, storage, settings, user=other, filename="khac.pdf", data=PDF_BYTES
        )
        job = (await s.execute(select(Job).where(Job.run_id == run_id))).scalar_one()
        job.status = JobStatus.DONE.value
        await s.commit()
        rows = await documents.list_documents(s, user=user)
    assert [r.name for r in rows] == ["bai.pdf"]
    assert rows[0].latest_run_id == run_id and rows[0].done_stages == 1
    assert rows[0].latest_run_status == RunStatus.QUEUED
