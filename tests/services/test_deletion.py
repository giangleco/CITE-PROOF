import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import FeedbackVerdict
from citeproof.core.errors import AuthError, NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    ClaimVerification,
    Document,
    Feedback,
    Invite,
    LLMCall,
    User,
    VerificationRun,
    WebSession,
    Work,
)
from citeproof.services import auth, deletion, documents, feedback
from citeproof.services.sample import seed_sample
from citeproof.storage import LocalStorage, document_prefix
from tests.files import PDF_BYTES
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]


async def _count(s: AsyncSession, model: type) -> int:
    return int(await s.scalar(select(func.count()).select_from(model)) or 0)


@pytest.mark.db
async def test_delete_uploaded_document_removes_rows_and_files(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await new_user(s)
        doc, run_id = await documents.create_file_document(
            s, storage, settings, user=user, filename="a.pdf", data=PDF_BYTES
        )
        s.add(
            LLMCall(
                run_id=run_id,
                task="W5",
                provider="p",
                model="m",
                prompt_version="v",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cache_key="k1",
                cache_namespace=f"doc:{doc.id}",
            )
        )
        await s.commit()
        key = doc.storage_key
        await deletion.delete_document(s, storage, user=user, document_id=doc.id)
        assert await _count(s, Document) == 0 and await _count(s, VerificationRun) == 0
        call = (await s.execute(select(LLMCall))).scalar_one()
    assert call.cache_key is None
    with pytest.raises(FileNotFoundError):
        await storage.get(key)


@pytest.mark.db
async def test_delete_sample_removes_feedback_and_sample_works_only(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        s.add(
            Work(
                doi="10.48550/arXiv.1706.03762",
                title="Attention",
                title_norm="attention",
                metadata_source="crossref",
                retrieved_at=datetime.now(UTC),
            )
        )
        user = await new_user(s)
        doc_id, _ = await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        cv = (await s.execute(select(ClaimVerification.id))).scalars().first()
        assert cv is not None
        await feedback.save_feedback(
            s, user=user, claim_verification_id=cv, verdict=FeedbackVerdict.AGREE, comment=None
        )
        await s.commit()
        await deletion.delete_document(s, storage, user=user, document_id=doc_id)
        works = (await s.execute(select(Work.metadata_source))).scalars().all()
        assert await _count(s, Feedback) == 0
    assert works == ["crossref"]


@pytest.mark.db
async def test_delete_foreign_document_404(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        owner = await new_user(s)
        other = await new_user(s, "o@b.vn")
        doc_id, _ = await seed_sample(s, settings, user_id=owner.id)
        await s.commit()
        with pytest.raises(NotFoundError):
            await deletion.delete_document(s, storage, user=other, document_id=doc_id)
        assert await _count(s, Document) == 1


@pytest.mark.db
async def test_orphan_sweep(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await new_user(s)
        orphan = document_prefix(user.id, uuid.uuid4())
        await storage.put(f"{orphan}/original.pdf", b"x")
        keep, _ = await documents.create_file_document(
            s, storage, settings, user=user, filename="giu.pdf", data=PDF_BYTES
        )
        gone, _ = await documents.create_file_document(
            s, storage, settings, user=user, filename="xoa.pdf", data=PDF_BYTES
        )
        await s.commit()
        await deletion.delete_document(s, storage, user=user, document_id=gone.id)
    assert await storage.list_children(f"documents/{user.id}") == [str(keep.id)]


@pytest.mark.db
async def test_delete_account_wrong_password(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await new_user(s)
        with pytest.raises(AuthError):
            await deletion.delete_account(s, storage, user=user, password="sai-mat-khau-roi")
        assert await _count(s, User) == 1


@pytest.mark.db
async def test_delete_account_everything(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await new_user(s)
        _, run_id = await documents.create_file_document(
            s, storage, settings, user=user, filename="a.pdf", data=PDF_BYTES
        )
        s.add(
            LLMCall(
                run_id=run_id,
                task="W5",
                provider="p",
                model="m",
                prompt_version="v",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cache_key="k",
                cache_namespace="x",
            )
        )
        await seed_sample(s, settings, user_id=user.id)
        await auth.create_invite(s, created_by=user.id)
        await auth.create_session(s, settings, user_id=user.id)
        await s.commit()
        await deletion.delete_account(s, storage, user=user, password="mat-khau-du-dai")
        for model in (User, Document, WebSession, Invite, Feedback):
            assert await _count(s, model) == 0, model
        call = (await s.execute(select(LLMCall))).scalar_one()
    assert call.cache_key is None and call.run_id is None
    assert await storage.list_children("documents") == []
