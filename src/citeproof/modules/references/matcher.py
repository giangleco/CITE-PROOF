"""Bộ so khớp tất định giữa tài liệu tham khảo và bản ghi metadata (spec §7.3, QĐ-20).

Tiêu đề so bằng `token_set_ratio` của rapidfuzz trên `fold(title_norm(...))`: NFC, chữ
thường, bỏ dấu câu rồi bỏ dấu tiếng Việt, nên chuỗi NFD hay lẫn chuẩn (sao chép từ PDF,
macOS) khớp bản ghi NFC (Review Focus #4).

Chấp nhận một ứng viên khi DOI trùng, hoặc điểm tiêu đề ≥ `TITLE_ACCEPT`, hoặc ≥ `TITLE_WEAK`
cùng họ tác giả đầu và năm lệch ≤ 1 (cần cả hai năm). `score` là điểm tiêu đề / 100, hoặc
1.0 khi DOI trùng.

Trường lệch chỉ báo khi cả bài lẫn bản ghi đều có giá trị: `title` (điểm < `TITLE_EXACT`),
`authors` (họ tác giả đầu khác), `year`, `doi` (bài ghi DOI khác). Không bao giờ báo `venue`
ở v1 vì tên viết tắt (NeurIPS…) gây báo lệch sai (QĐ-20).

Họ tác giả trong bài theo `family_name` (phần trước dấu phẩy; họ Việt phổ biến đứng đầu; còn
lại từ cuối). Họ của bản ghi lấy cả trường `family`, từ cuối của nó, và họ đoán lại từ tên đầy
đủ "given family": OpenAlex và Semantic Scholar đoán họ là từ cuối của tên hiển thị, nên
"Nguyễn Văn An" có `family = "An"`; đoán lại từ tên đầy đủ cho "Nguyễn".

Ứng viên tốt nhất xếp theo `score` rồi ít trường lệch hơn. Sát điểm (`close_call`) khi một
ứng viên khác cũng được chấp nhận, kém dưới `CLOSE_DELTA` điểm và không lệch nhiều trường hơn
ứng viên đầu: bản preprint lệch năm so với bài không làm bản chính thức thành mơ hồ.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

from citeproof.core.domain import ParsedReference
from citeproof.integrations.metadata import WorkAuthor, WorkRecord
from citeproof.modules.references.normalize import family_name, fold, normalize_doi, title_norm

TITLE_ACCEPT = 90
TITLE_WEAK = 75
TITLE_EXACT = 95
CLOSE_DELTA = 5.0
# Năm lệch tối đa khi chấp nhận qua tiêu đề gần đúng (`TITLE_WEAK`).
MAX_YEAR_DELTA = 1

MismatchField = Literal["title", "authors", "year", "venue", "doi"]
_NON_ALNUM = re.compile(r"[\W_]+")


class Mismatch(BaseModel):
    """Một trường lệch (`08` §4.4a): giá trị trong bài và giá trị của bản ghi tìm thấy."""

    model_config = ConfigDict(frozen=True)

    field: MismatchField
    in_document: str | None
    found: str | None


@dataclass(frozen=True)
class MatchResult:
    accepted: bool
    score: float
    title_score: float
    mismatches: tuple[Mismatch, ...]


@dataclass(frozen=True)
class BestMatch:
    record: WorkRecord
    result: MatchResult
    close_call: bool


def title_similarity(left: str | None, right: str | None) -> float:
    """`token_set_ratio` (0 đến 100) của hai tiêu đề đã chuẩn hóa và gấp dấu; thiếu một bên → 0."""
    a = fold(title_norm(left)) if left else ""
    b = fold(title_norm(right)) if right else ""
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a, b))


def _name_key(text: str) -> str:
    return _NON_ALNUM.sub("", fold(text))


def _record_family_keys(author: WorkAuthor) -> set[str]:
    keys = {_name_key(author.family)}
    tokens = author.family.split()
    if tokens:
        keys.add(_name_key(tokens[-1]))
    if author.given:
        keys.add(_name_key(family_name(f"{author.given} {author.family}")))
    keys.discard("")
    return keys


def first_author_matches(ref_author: str, author: WorkAuthor) -> bool:
    """Họ tác giả viết trong bài có khớp tác giả của bản ghi không (bỏ dấu, chữ thường)."""
    key = _name_key(family_name(ref_author))
    return bool(key) and key in _record_family_keys(author)


def _display(author: WorkAuthor) -> str:
    return " ".join(part for part in (author.given, author.family) if part)


def _has_title(ref: ParsedReference) -> bool:
    return bool(ref.title and title_norm(ref.title))


def match_candidate(ref: ParsedReference, cand: WorkRecord) -> MatchResult:
    """So một ứng viên với trường đã parse của tài liệu tham khảo (spec §7.3)."""
    title_score = title_similarity(ref.title, cand.title)
    ref_doi = normalize_doi(ref.doi)
    cand_doi = normalize_doi(cand.doi)
    doi_equal = ref_doi is not None and ref_doi == cand_doi
    ref_author = ref.authors[0] if ref.authors else None
    cand_author = cand.authors[0] if cand.authors else None
    same_author = (
        None
        if ref_author is None or cand_author is None
        else first_author_matches(ref_author, cand_author)
    )
    year_delta = (
        abs(ref.year - cand.year) if ref.year is not None and cand.year is not None else None
    )
    accepted = (
        doi_equal
        or title_score >= TITLE_ACCEPT
        or (
            title_score >= TITLE_WEAK
            and same_author is True
            and year_delta is not None
            and year_delta <= MAX_YEAR_DELTA
        )
    )
    mismatches: list[Mismatch] = []
    if _has_title(ref) and title_score < TITLE_EXACT:
        mismatches.append(Mismatch(field="title", in_document=ref.title, found=cand.title))
    if same_author is False and ref_author is not None and cand_author is not None:
        mismatches.append(
            Mismatch(field="authors", in_document=ref_author, found=_display(cand_author))
        )
    if year_delta:
        mismatches.append(Mismatch(field="year", in_document=str(ref.year), found=str(cand.year)))
    if ref_doi is not None and cand_doi is not None and not doi_equal:
        doi_text = ref.doi.strip() if ref.doi else ref_doi
        mismatches.append(Mismatch(field="doi", in_document=doi_text, found=cand_doi))
    return MatchResult(
        accepted=accepted,
        score=1.0 if doi_equal else title_score / 100,
        title_score=title_score,
        mismatches=tuple(mismatches),
    )


def pick_best(ref: ParsedReference, cands: Sequence[WorkRecord]) -> BestMatch | None:
    """Ứng viên được chấp nhận tốt nhất (thứ tự nguồn giữ khi hòa); không có → None."""
    accepted = [(cand, result) for cand in cands if (result := match_candidate(ref, cand)).accepted]
    if not accepted:
        return None
    accepted.sort(key=lambda item: (-item[1].score, len(item[1].mismatches)))
    record, best = accepted[0]
    close_call = any(
        (best.score - other.score) * 100 < CLOSE_DELTA
        and len(other.mismatches) <= len(best.mismatches)
        for _, other in accepted[1:]
    )
    return BestMatch(record=record, result=best, close_call=close_call)
