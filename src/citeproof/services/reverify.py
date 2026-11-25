"""Kiểm chứng lại một phần (FR-34, T-505, QĐ-30, spec §10).

Hai loại lần chạy con của một lần chạy đã xong:

- `upload`: các tài liệu ở tab "Thiếu toàn văn" có tệp tải lên sau khi lần chạy được tạo;
  bắt đầu từ S6 (lập chỉ mục tệp tải lên, truy xuất, đối chiếu lại các cặp của chúng).
- `lookup_retry`: các tài liệu `LOOKUP_FAILED`; bắt đầu từ S3 (tra lại rồi lấy bản mở, lập
  chỉ mục, đối chiếu lại).

Lần chạy con giữ nguyên cấu hình đã đóng băng của lần chạy cha (QĐ-46), thêm `scope` và bỏ
`demo`.
S9 của nó chép phần còn lại từ lần chạy cha (`modules.reports.merge`). Một tài liệu chỉ có một
lần chạy hoạt động (`documents.lock_for_new_run`); lần chạy con không ghi `UsageEvent` nên
không tính vào hạn mức.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import ReferenceStatus, RunStatus, StageName
from citeproof.core.errors import InputError, NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import ReferenceCheck, ReferenceEntry, User, VerificationRun
from citeproof.pipeline.config import RunConfig, RunScope
from citeproof.pipeline.runs import create_partial_run
from citeproof.services.documents import lock_for_new_run
from citeproof.services.report import list_missing
from citeproof.web import labels

__all__ = ["FINISHED", "start_lookup_retry", "start_reverify_uploads"]

# Lần chạy cha phải đã xong; trang báo cáo dùng cùng tập này để ẩn/hiện nút.
FINISHED: frozenset[RunStatus] = frozenset({RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS})


async def _finished_parent(session: AsyncSession, *, user: User, run_id: UUID) -> VerificationRun:
    """Lần chạy cha của chính người dùng, đã khóa tài liệu và đã xong."""
    run = (
        await session.execute(
            select(VerificationRun).where(
                VerificationRun.id == run_id, VerificationRun.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError()
    await lock_for_new_run(session, user=user, document_id=run.document_id)
    if RunStatus(run.status) not in FINISHED:
        raise InputError(labels.RUN_NOT_FINISHED, code="RUN_NOT_FINISHED")
    return run


async def _start_child(
    session: AsyncSession,
    parent: VerificationRun,
    *,
    kind: str,
    reference_entry_ids: list[UUID],
    start: StageName,
) -> UUID:
    scope = RunScope.model_validate(
        {"kind": kind, "reference_entry_ids": reference_entry_ids, "from_stage": start}
    )
    # `demo` (chỉ môi trường local) không áp cho lần chạy con: `fail_at` của lần chạy cha
    # sẽ làm lần chạy con lỗi theo.
    config = RunConfig.load(parent.config).model_copy(update={"scope": scope, "demo": None})
    return await create_partial_run(
        session,
        parent_run_id=parent.id,
        user_id=parent.user_id,
        document_id=parent.document_id,
        config=config.model_dump(mode="json"),
        start=start,
    )


async def start_reverify_uploads(
    session: AsyncSession, settings: Settings, *, user: User, run_id: UUID
) -> UUID:
    """Lần chạy con đối chiếu lại các tài liệu có tệp tải lên mới (bắt đầu từ S6)."""
    parent = await _finished_parent(session, user=user, run_id=run_id)
    _, missing = await list_missing(session, user=user, run_id=run_id)
    scope = [item.reference_entry_id for item in missing if item.uploaded and item.uploaded.is_new]
    if not scope:
        raise InputError(labels.NO_NEW_UPLOADS, code="NO_NEW_UPLOADS")
    return await _start_child(
        session, parent, kind="upload", reference_entry_ids=scope, start=StageName.S6
    )


async def start_lookup_retry(
    session: AsyncSession, settings: Settings, *, user: User, run_id: UUID
) -> UUID:
    """Lần chạy con tra lại các tài liệu `LOOKUP_FAILED` (bắt đầu từ S3)."""
    parent = await _finished_parent(session, user=user, run_id=run_id)
    scope = list(
        (
            await session.execute(
                select(ReferenceCheck.reference_entry_id)
                .join(ReferenceEntry, ReferenceEntry.id == ReferenceCheck.reference_entry_id)
                .where(
                    ReferenceCheck.run_id == run_id,
                    ReferenceCheck.status == ReferenceStatus.LOOKUP_FAILED.value,
                )
                .order_by(ReferenceEntry.order_index)
            )
        ).scalars()
    )
    if not scope:
        raise InputError(labels.NO_FAILED_LOOKUPS, code="NO_FAILED_LOOKUPS")
    return await _start_child(
        session, parent, kind="lookup_retry", reference_entry_ids=scope, start=StageName.S3
    )
