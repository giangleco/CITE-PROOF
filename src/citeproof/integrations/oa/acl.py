"""Bản mở trên ACL Anthology (spec §7.5): DOI `10.18653/v1/<id>` → `aclanthology.org/<id>.pdf`.

ID kiểu cũ (`N19-1423`) viết hoa như trên Anthology (DOI chuẩn hóa ở dạng chữ thường); ID kiểu
mới (`2020.acl-main.1`) giữ nguyên. Bài trên Anthology theo giấy phép CC BY 4.0.
"""

import re

from citeproof.integrations.oa.base import AccessOption, WorkRef

DOI_PREFIX = "10.18653/v1/"
PDF_BASE = "https://aclanthology.org/"
LICENSE = "CC BY 4.0"
_OLD_STYLE = re.compile(r"[a-z]\d{2}-\d{4}")
# Chỉ ký tự có trong ID của Anthology: không có `/`, `?`, `#` lọt vào URL.
_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9.-]*")


class AclAccess:
    async def resolve(self, work: WorkRef) -> list[AccessOption]:
        doi = (work.doi or "").strip().lower()
        if not doi.startswith(DOI_PREFIX):
            return []
        anthology_id = doi.removeprefix(DOI_PREFIX)
        if not _SAFE_ID.fullmatch(anthology_id) or ".." in anthology_id:
            return []
        if _OLD_STYLE.fullmatch(anthology_id):
            anthology_id = anthology_id.upper()
        return [AccessOption(url=f"{PDF_BASE}{anthology_id}.pdf", license=LICENSE, provider="acl")]
