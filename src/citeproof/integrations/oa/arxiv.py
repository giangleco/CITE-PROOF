"""Bản mở trên arXiv (spec §7.5): `https://export.arxiv.org/pdf/<id>`.

ID lấy từ `works.arxiv_id`, không có thì từ DOI arXiv (`10.48550/arXiv.<id>`). ID phải đúng
dạng arXiv (mới `1706.03762`, cũ `cs/0112017`, có thể kèm phiên bản) mới được ghép vào URL.
arXiv không có một giấy phép chung cho mọi bài nên `license` để trống.
"""

import re

from citeproof.integrations.metadata.base import arxiv_id as parse_arxiv_id
from citeproof.integrations.oa.base import AccessOption, WorkRef

PDF_BASE = "https://export.arxiv.org/pdf/"
_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[a-z]{2})?/\d{7})(?:v\d+)?", re.IGNORECASE)


class ArxivAccess:
    async def resolve(self, work: WorkRef) -> list[AccessOption]:
        arxiv = work.arxiv_id or parse_arxiv_id(work.doi)
        if not arxiv or not _ID.fullmatch(arxiv):
            return []
        return [AccessOption(url=f"{PDF_BASE}{arxiv}", license=None, provider="arxiv")]
