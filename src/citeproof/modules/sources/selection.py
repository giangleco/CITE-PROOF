"""Chọn nguồn cho một lần chạy, viết một lần cho S5, S6 và S7 (preflight F33).

`run_work_ids` trả các work mà lần chạy cần nguồn: `reference_checks` của lần chạy (hoặc của lần
chạy cha cho lần chạy con) có `work_id` và tài liệu tham khảo
thuộc phạm vi (`RunInfo.in_scope`). S5 lấy bản mở cho chúng.
`relevant_source_files` dựng trên cùng hàm này: S6 lập chỉ mục đúng các tệp đó và S7 chọn nguồn
trong đó, để nguồn được lập chỉ mục và nguồn được dùng không lệch nhau.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import SourceOrigin
from citeproof.db.models import ReferenceCheck, ReferenceEntry, SourceFile
from citeproof.pipeline.config import RunInfo


async def entry_works(
    session: AsyncSession,
    info: RunInfo,
    *,
    reference_entry_id: UUID | None = None,
    scoped: bool = True,
) -> list[tuple[UUID, UUID]]:
    """`(reference_entry_id, work_id)` của các tài liệu tham khảo có work, theo thứ tự trong bài.

    Lấy `reference_checks` của lần chạy; lần chạy con (kiểm chứng lại một phần bắt đầu từ S6)
    không có dòng riêng cho reference chưa tra lại nên dùng dòng của lần chạy cha. Dòng của lần
    chạy này luôn thắng dòng của lần chạy cha. `reference_entry_id` thu hẹp vào một reference và
    `scoped=False` bỏ lọc phạm vi (S7 chọn nguồn cho một reference đã được `load_pairs` lọc).
    """
    run_ids = [info.run_id] if info.parent_run_id is None else [info.run_id, info.parent_run_id]
    stmt = (
        select(ReferenceCheck.reference_entry_id, ReferenceCheck.work_id, ReferenceCheck.run_id)
        .join(ReferenceEntry, ReferenceEntry.id == ReferenceCheck.reference_entry_id)
        .where(ReferenceCheck.run_id.in_(run_ids), ReferenceCheck.work_id.is_not(None))
        .order_by(ReferenceEntry.order_index, ReferenceEntry.id)
    )
    if reference_entry_id is not None:
        stmt = stmt.where(ReferenceCheck.reference_entry_id == reference_entry_id)
    rows = (await session.execute(stmt)).all()
    chosen: dict[UUID, tuple[UUID, UUID]] = {}
    for entry_id, work_id, run_id in rows:
        if work_id is None or (scoped and not info.in_scope(entry_id)):
            continue
        if entry_id not in chosen or run_id == info.run_id:
            chosen[entry_id] = (run_id, work_id)
    order = list(dict.fromkeys(entry_id for entry_id, _, _ in rows))
    return [(entry_id, chosen[entry_id][1]) for entry_id in order if entry_id in chosen]


async def run_work_ids(session: AsyncSession, info: RunInfo) -> list[UUID]:
    """Work của các tài liệu tham khảo trong phạm vi, theo thứ tự trong bài, không trùng."""
    return list(dict.fromkeys(work_id for _, work_id in await entry_works(session, info)))


async def relevant_source_files(session: AsyncSession, info: RunInfo) -> list[SourceFile]:
    """Tệp nguồn của lần chạy: bản mở dùng chung của các work (`run_work_ids`), rồi tệp tải lên.

    Tệp `USER_UPLOAD` chỉ được tính khi thuộc một tài liệu tham khảo của chính tài liệu của lần
    chạy, trong phạm vi, và do chính người dùng của lần chạy tải lên: tệp của người khác cho cùng
    work không bao giờ được chọn. Bản mở theo thứ tự work rồi mức nội dung; tệp tải lên theo thứ
    tự tài liệu tham khảo rồi thời điểm tải.
    """
    work_ids = await run_work_ids(session, info)
    files: list[SourceFile] = []
    if work_ids:
        position = {work_id: index for index, work_id in enumerate(work_ids)}
        open_files = (
            await session.execute(
                select(SourceFile)
                .where(
                    SourceFile.origin == SourceOrigin.OPEN_ACCESS.value,
                    SourceFile.work_id.in_(work_ids),
                )
                .order_by(SourceFile.content_level, SourceFile.created_at, SourceFile.id)
            )
        ).scalars()
        files.extend(
            sorted(
                open_files,
                key=lambda f: position[f.work_id] if f.work_id is not None else len(position),
            )
        )
    uploads = (
        await session.execute(
            select(SourceFile)
            .join(ReferenceEntry, ReferenceEntry.id == SourceFile.reference_entry_id)
            .where(
                SourceFile.origin == SourceOrigin.USER_UPLOAD.value,
                SourceFile.owner_user_id == info.user_id,
                ReferenceEntry.document_id == info.document_id,
            )
            .order_by(ReferenceEntry.order_index, SourceFile.created_at, SourceFile.id)
        )
    ).scalars()
    files.extend(f for f in uploads if info.in_scope(f.reference_entry_id))
    return files
