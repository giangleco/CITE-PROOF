"""Helper dựng tài liệu và lần chạy cho test dịch vụ và trang."""

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import JobStatus, RunStatus, StageName, UserRole
from citeproof.core.settings import Settings
from citeproof.db.models import Job, User, VerificationRun
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
