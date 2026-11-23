"""Xóa tài liệu và xóa tài khoản theo `08` §4.5 (FR-62, phần giao diện của T-605).

Thứ tự khi xóa tài liệu: phản hồi trỏ tới kết quả của tài liệu; bỏ liên kết nội dung
của `llm_calls` (`cache_key = NULL`); xóa tài liệu (cascade); xóa `works` mẫu không còn
ai dùng; commit; rồi mới xóa file trong storage và dọn thư mục mồ côi của người dùng.
Lỗi xóa file chỉ được log, không chặn người dùng; lần xóa sau sẽ dọn lại.
"""

import logging
from uuid import UUID

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.errors import AuthError, NotFoundError
from citeproof.db.models import (
    ClaimVerification,
    Document,
    Feedback,
    Invite,
    LLMCall,
    ReferenceCheck,
    SourceFile,
    User,
    VerificationRun,
    WebSession,
    Work,
)
from citeproof.services import auth
from citeproof.services.sample import SAMPLE_METADATA_SOURCE
from citeproof.storage import Storage, document_prefix

logger = logging.getLogger(__name__)


async def _purge_document_rows(session: AsyncSession, document: Document) -> None:
    run_ids = select(VerificationRun.id).where(VerificationRun.document_id == document.id)
    cv_ids = select(ClaimVerification.id).where(ClaimVerification.run_id.in_(run_ids))
    rc_ids = select(ReferenceCheck.id).where(ReferenceCheck.run_id.in_(run_ids))
    work_ids = list(
        (
            await session.execute(
                select(ReferenceCheck.work_id).where(
                    ReferenceCheck.run_id.in_(run_ids), ReferenceCheck.work_id.is_not(None)
                )
            )
        ).scalars()
    )
    await session.execute(
        delete(Feedback).where(or_(Feedback.target_id.in_(cv_ids), Feedback.target_id.in_(rc_ids)))
    )
    await session.execute(
        update(LLMCall)
        .where(
            or_(
                LLMCall.run_id.in_(run_ids),
                LLMCall.cache_namespace == f"doc:{document.id}",
            )
        )
        .values(cache_key=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(delete(Document).where(Document.id == document.id))
    if work_ids:
        await session.execute(
            delete(Work).where(
                Work.id.in_(work_ids),
                Work.metadata_source == SAMPLE_METADATA_SOURCE,
                ~exists().where(ReferenceCheck.work_id == Work.id),
                ~exists().where(SourceFile.work_id == Work.id),
            )
        )


async def _cleanup_storage(
    session: AsyncSession, storage: Storage, user_id: UUID, removed: list[UUID]
) -> None:
    try:
        for document_id in removed:
            await storage.delete_prefix(document_prefix(user_id, document_id))
        known = {
            str(i)
            for i in (
                await session.execute(select(Document.id).where(Document.user_id == user_id))
            ).scalars()
        }
        for child in await storage.list_children(f"documents/{user_id}"):
            if child not in known:
                await storage.delete_prefix(f"documents/{user_id}/{child}")
    except (OSError, ValueError) as exc:
        logger.warning("storage_cleanup_failed", extra={"error_type": type(exc).__name__})


async def delete_document(
    session: AsyncSession, storage: Storage, *, user: User, document_id: UUID
) -> None:
    document = (
        await session.execute(
            select(Document).where(Document.id == document_id, Document.user_id == user.id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError()
    await _purge_document_rows(session, document)
    await session.commit()
    await _cleanup_storage(session, storage, user.id, [document_id])


async def delete_account(
    session: AsyncSession, storage: Storage, *, user: User, password: str
) -> None:
    if not auth.verify_password(user.password_hash, password):
        raise AuthError("Mật khẩu không đúng.", code="INVALID_CREDENTIALS")
    user_id = user.id
    documents = list(
        (await session.execute(select(Document).where(Document.user_id == user_id))).scalars()
    )
    for document in documents:
        await _purge_document_rows(session, document)
    await session.execute(delete(Feedback).where(Feedback.user_id == user_id))
    await session.execute(
        delete(Invite).where(Invite.created_by == user_id, Invite.used_at.is_(None))
    )
    await session.execute(delete(WebSession).where(WebSession.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()
    try:
        await storage.delete_prefix(f"documents/{user_id}")
    except (OSError, ValueError) as exc:
        logger.warning("storage_cleanup_failed", extra={"error_type": type(exc).__name__})
