"""Helper dựng tài liệu và lần chạy cho test dịch vụ và trang."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import (
    ClaimStatus,
    ContentLevelUsed,
    JobStatus,
    LinkMethod,
    RunStatus,
    StageName,
    UnverifiableReason,
    UserRole,
    Verifier,
)
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    ClaimVerification,
    Job,
    User,
    VerificationRun,
)
from citeproof.services import auth, documents
from citeproof.storage import LocalStorage
from tests.files import PDF_BYTES


async def new_user(s: AsyncSession, email: str = "u@b.vn", role: UserRole = UserRole.USER) -> User:
    user = await auth.create_user(
        s, email=email, password="mat-khau-du-dai", display_name="U", role=role
    )
    await s.commit()
    return user


async def new_run(
    s: AsyncSession,
    settings: Settings,
    user: User,
    *,
    config_demo: documents.DemoConfig | None = None,
) -> UUID:
    _, run_id = await documents.create_file_document(
        s,
        LocalStorage(settings.storage_dir),
        settings,
        user=user,
        filename="bai.pdf",
        data=PDF_BYTES,
        demo=config_demo,
    )
    await s.commit()
    return run_id


async def set_jobs(s: AsyncSession, run_id: UUID, states: dict[StageName, JobStatus]) -> None:
    """Đặt trạng thái job cho các bước; thêm job nếu bước chưa có."""
    for stage, status in states.items():
        result = await s.execute(
            update(Job)
            .where(Job.run_id == run_id, Job.stage == stage.value)
            .values(status=status.value)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            s.add(Job(run_id=run_id, stage=stage.value, status=status.value))
    await s.commit()


async def set_run(s: AsyncSession, run_id: UUID, **values: object) -> None:
    if "status" in values and isinstance(values["status"], RunStatus):
        values["status"] = values["status"].value
    await s.execute(update(VerificationRun).where(VerificationRun.id == run_id).values(**values))
    await s.commit()


UNRESOLVED_SENTENCE = "Một nhận định có trích dẫn chưa trỏ được tới tài liệu nào [9]."


async def run_document_id(s: AsyncSession, run_id: UUID) -> UUID:
    document_id = await s.scalar(
        select(VerificationRun.document_id).where(VerificationRun.id == run_id)
    )
    assert document_id is not None
    return document_id


async def add_claim_verification(
    s: AsyncSession,
    run_id: UUID,
    *,
    entry_id: UUID | None,
    sentence: str,
    status: ClaimStatus = ClaimStatus.UNVERIFIABLE,
    reason: UnverifiableReason | None = UnverifiableReason.REFERENCE_UNRESOLVED,
    verifier: Verifier = Verifier.V2,
    page: int = 30,
) -> UUID:
    """Thêm citation + claim + dòng `claim_verifications` (như S4 và S8 ghi) vào lần chạy."""
    document_id = await run_document_id(s, run_id)
    citation = Citation(
        document_id=document_id,
        marker_text="[9]",
        sentence_text=sentence,
        page=page,
        char_start=10_000 + page,
        char_end=10_000 + page + len(sentence),
        section=None,
    )
    s.add(citation)
    await s.flush()
    s.add(
        CitationLink(
            citation_id=citation.id,
            reference_entry_id=entry_id,
            link_method=LinkMethod.NUMERIC.value,
        )
    )
    claim = Claim(
        document_id=document_id,
        citation_id=citation.id,
        claim_text=sentence,
        context_text=sentence,
        language="vi",
        extraction_method="rule",
    )
    s.add(claim)
    await s.flush()
    cv = ClaimVerification(
        run_id=run_id,
        claim_id=claim.id,
        reference_entry_id=entry_id,
        status=status.value,
        unverifiable_reason=reason.value if reason else None,
        content_level_used=ContentLevelUsed.NONE.value,
        explanation="Chưa xác định được tài liệu.",
        needs_user_review=False,
        model="none",
        prompt_version="none",
        schema_version="verdict/2",
        verifier=verifier.value,
    )
    s.add(cv)
    await s.flush()
    return cv.id
