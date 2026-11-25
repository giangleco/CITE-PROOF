"""Truy vấn lexical trong một nguồn: tsquery từ claim và `ts_rank_cd` (spec §7.7, QĐ-24).

`build_tsquery` chỉ phát ra token `\\w+` được đặt trong nháy đơn và nối bằng `|`, nên văn bản
bất kỳ của claim không thể thành toán tử tsquery. `search_chunks` luôn giới hạn theo một
`source_file_id`.
"""

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.db.models import SourceChunk

__all__ = ["STOPWORDS", "LexicalHit", "build_tsquery", "search_chunks"]

_TOKEN = re.compile(r"\w+")
_TOKEN_OR_DECIMAL = re.compile(r"(?P<decimal>\d+[.,]\d+)|\w+")

_EN = (
    "a an and are as at be been being but by can could did do does for from had has have "
    "he her his i if in into is it its may might of on or our she should so such than that "
    "the their them then there these they this those to was we were what when where which "
    "while who will with would you your not no also more most other some any all each both "
    "via per than very can just only over under between about after before during"
)
_VI = (
    "và của là các những một có được cho trong với để không này đó khi như thì mà cũng đã "
    "sẽ đang bị bởi từ theo về tại trên dưới sau trước hay hoặc nhưng nếu vì nên rất còn "
    "lại ra vào lên các mỗi mọi đều chỉ cả nào gì đây kia ấy thế sao đâu"
)
STOPWORDS: frozenset[str] = frozenset(
    unicodedata.normalize("NFC", word) for word in (_EN + " " + _VI).split()
)


def _tokens(text: str) -> list[str]:
    """Token `\\w+` theo thứ tự; mỗi số thập phân thêm một token `95.7` (dấu `,` đổi thành `.`).

    Parser `simple` của PostgreSQL lập chỉ mục `95.7` thành một token, nên token `\\w+` (`95`, `7`)
    một mình không bao giờ khớp số thập phân.
    """
    out: list[str] = []
    for match in _TOKEN_OR_DECIMAL.finditer(unicodedata.normalize("NFC", text).lower()):
        word = match.group()
        if match.group("decimal"):
            out.append(word.replace(",", "."))
            out.extend(_TOKEN.findall(word))
        else:
            out.append(word)
    return out


def build_tsquery(text: str, *, max_terms: int = 32) -> str | None:
    """Token của `text` (NFC, chữ thường, ≥ 2 ký tự, không stopword) nối OR; None nếu rỗng."""
    seen: dict[str, None] = {}
    for token in _tokens(text):
        if len(token) >= 2 and token not in STOPWORDS:
            seen.setdefault(token)
        if len(seen) >= max_terms:
            break
    if not seen:
        return None
    return " | ".join("'" + token.replace("'", "''") + "'" for token in seen)


@dataclass(frozen=True)
class LexicalHit:
    chunk_id: UUID
    chunk_index: int
    score: float


async def search_chunks(
    session: AsyncSession,
    source_file_id: UUID,
    tsquery: str,
    k: int,
    *,
    section: str | None = None,
) -> list[LexicalHit]:
    """Top-`k` chunk của một tệp khớp `tsquery`, theo điểm giảm dần rồi `chunk_index`."""
    query = func.to_tsquery("simple", tsquery)
    score = func.ts_rank_cd(SourceChunk.tsv, query).label("score")
    stmt = (
        select(SourceChunk.id, SourceChunk.chunk_index, score)
        .where(SourceChunk.source_file_id == source_file_id, SourceChunk.tsv.op("@@")(query))
        .order_by(score.desc(), SourceChunk.chunk_index)
        .limit(k)
    )
    if section is not None:
        stmt = stmt.where(SourceChunk.section == section)
    rows = (await session.execute(stmt)).all()
    return [LexicalHit(row.id, row.chunk_index, float(row.score)) for row in rows]
