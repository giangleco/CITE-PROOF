"""Chọn nguồn cho một lần chạy, viết một lần cho S5, S6 và S7 (preflight F33).

`run_work_ids` trả các work mà lần chạy cần nguồn: `reference_checks` của lần chạy có
`work_id` và tài liệu tham khảo thuộc phạm vi (`RunInfo.in_scope`). S5 lấy bản mở cho chúng;
S6 (Task 13) dựng `relevant_source_files` trên cùng hàm này, và S7 chọn nguồn qua đó, để nguồn
được lập chỉ mục và nguồn được dùng không lệch nhau.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.db.models import ReferenceCheck, ReferenceEntry
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
