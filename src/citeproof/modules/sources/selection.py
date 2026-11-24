"""Chọn nguồn cho một lần chạy, viết một lần cho S5, S6 và S7 (preflight F33).

`run_work_ids` trả các work mà lần chạy cần nguồn: `reference_checks` của lần chạy có
`work_id` và tài liệu tham khảo thuộc phạm vi (`RunInfo.in_scope`). S5 lấy bản mở cho chúng.
`relevant_source_files` dựng trên cùng hàm này: S6 lập chỉ mục đúng các tệp đó và S7 chọn nguồn
trong đó, để nguồn được lập chỉ mục và nguồn được dùng không lệch nhau.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import SourceOrigin
from citeproof.db.models import ReferenceCheck, ReferenceEntry, SourceFile
from citeproof.pipeline.config import RunInfo


async def run_work_ids(session: AsyncSession, info: RunInfo) -> list[UUID]:
    """Work của các tài liệu tham khảo trong phạm vi, theo thứ tự trong bài, không trùng."""
    rows = (
        await session.execute(
            select(ReferenceCheck.reference_entry_id, ReferenceCheck.work_id)
            .join(ReferenceEntry, ReferenceEntry.id == ReferenceCheck.reference_entry_id)
            .where(ReferenceCheck.run_id == info.run_id, ReferenceCheck.work_id.is_not(None))
            .order_by(ReferenceEntry.order_index, ReferenceEntry.id)
        )
    ).all()
    ids = [work_id for entry_id, work_id in rows if work_id is not None and info.in_scope(entry_id)]
    return list(dict.fromkeys(ids))


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
