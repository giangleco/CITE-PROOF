"""Helper tạo dòng tối thiểu (chỉ cột NOT NULL, giá trị hợp lệ đơn giản)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import DocumentKind, ParseMethod, RefType
from citeproof.db.models import Citation, Claim, Document, User, VerificationRun
from citeproof.db.models.references import ReferenceEntry


def _uid() -> str:
    return uuid.uuid4().hex[:12]


async def make_user(session: AsyncSession) -> User:
    user = User(email=f"{_uid()}@example.test", password_hash="x", display_name="Test")
    session.add(user)
    await session.flush()
    return user


async def make_document(session: AsyncSession, user: User | None = None) -> Document:
    user = user or await make_user(session)
    doc = Document(
        user_id=user.id,
        kind=DocumentKind.TEXT.value,
        filename="doc.txt",
        mime_type="text/plain",
        storage_key=f"docs/{_uid()}",
        sha256=_uid(),
    )
    session.add(doc)
    await session.flush()
    return doc


async def make_run(session: AsyncSession, document: Document) -> VerificationRun:
    run = VerificationRun(document_id=document.id, user_id=document.user_id)
    session.add(run)
    await session.flush()
    return run


async def make_reference_entry(session: AsyncSession, document: Document) -> ReferenceEntry:
    entry = ReferenceEntry(
        document_id=document.id,
        order_index=0,
        raw_text="Ref",
        ref_type=RefType.ARTICLE.value,
        parse_method=ParseMethod.RULE.value,
    )
    session.add(entry)
    await session.flush()
    return entry


async def make_claim(session: AsyncSession, document: Document) -> Claim:
    citation = Citation(
        document_id=document.id,
        marker_text="[1]",
        sentence_text="Câu có trích dẫn [1].",
        char_start=0,
        char_end=10,
    )
    session.add(citation)
    await session.flush()
    claim = Claim(
        document_id=document.id,
        citation_id=citation.id,
        claim_text="Câu có trích dẫn",
        context_text="",
        language="vi",
        extraction_method="rule",
    )
    session.add(claim)
    await session.flush()
    return claim
