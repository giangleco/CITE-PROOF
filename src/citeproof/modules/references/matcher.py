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
lại từ cuối). Họ của bản ghi lấy cả trường `family`, từ cuối của nó, và — chỉ khi phần `given`
có từ hai từ trở lên — họ đoán lại từ tên đầy đủ "given family": OpenAlex và Semantic Scholar
đoán họ là từ cuối của tên hiển thị, nên "Nguyễn Văn An" có `family = "An"`, đoán lại cho
"Nguyễn"; "Le Song" (`given = "Le"`) không đoán lại, nên "Le, Q." vẫn là tác giả khác.

Hai quy tắc thêm (quyết định của controller sau Task 9):
- DOI trùng nhưng bài có tiêu đề và điểm tiêu đề < `DOI_TITLE_CONFLICT`: vẫn chấp nhận (DOI trỏ
  tới bản ghi này, giữ liên kết work) nhưng `needs_review`; trường lệch `title` làm trạng thái
  thành `METADATA_MISMATCH`.
- Tiêu đề ngắn hoặc tiêu đề con: khi tiêu đề của bài hay của ứng viên dưới `SHORT_TITLE_TOKENS`
  từ (sau `fold(title_norm(...))`), hoặc ứng viên có ít hơn `SUBSET_TOKEN_RATIO` số từ của bài,
  chấp nhận qua điểm tiêu đề (không có DOI trùng) còn cần năm trùng hoặc cùng họ tác giả đầu:
  `token_set_ratio` cho 100 khi mọi từ của tiêu đề này nằm trong tiêu đề kia ("Deep Learning"
  so với "A survey on deep learning for natural language processing").

Ứng viên tốt nhất xếp theo ít trường lệch hơn, rồi `score` (vòng sửa 1): ứng viên lệch tác giả
và năm không thắng bài đúng chỉ vì điểm tiêu đề cao hơn. Sát điểm (`close_call`) khi một ứng
viên khác cũng được chấp nhận, kém dưới `CLOSE_DELTA` điểm và không lệch nhiều trường hơn ứng
viên đầu: bản preprint lệch năm so với bài không làm bản chính thức thành mơ hồ.
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
# DOI trùng mà điểm tiêu đề dưới ngưỡng này: tiêu đề rõ ràng khác, gắn cờ tự kiểm tra.
DOI_TITLE_CONFLICT = 60
# Tiêu đề có ít từ hơn ngưỡng này cần thêm năm hoặc họ tác giả đầu khớp.
SHORT_TITLE_TOKENS = 4
# Ứng viên có ít hơn tỷ lệ này số từ tiêu đề của bài (tiêu đề con) cũng cần như trên.
SUBSET_TOKEN_RATIO = 0.6
# Chỉ đoán lại họ đứng đầu (tên Việt) khi phần `given` của bản ghi có ít nhất chừng này từ.
REGUESS_MIN_GIVEN_TOKENS = 2

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
    # DOI trùng nhưng tiêu đề rõ ràng khác (`DOI_TITLE_CONFLICT`): người dùng nên tự kiểm tra.
    needs_review: bool = False


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


def record_family_keys(author: WorkAuthor) -> set[str]:
    """Các dạng họ (đã gấp dấu, bỏ ký tự không phải chữ/số) chấp nhận cho tác giả của bản ghi."""
    keys = {_name_key(author.family)}
    tokens = author.family.split()
    if tokens:
        keys.add(_name_key(tokens[-1]))
    if author.given and len(author.given.split()) >= REGUESS_MIN_GIVEN_TOKENS:
        keys.add(_name_key(family_name(f"{author.given} {author.family}")))
    keys.discard("")
    return keys


def first_author_matches(ref_author: str, author: WorkAuthor) -> bool:
    """Họ tác giả viết trong bài có khớp tác giả của bản ghi không (bỏ dấu, chữ thường)."""
    key = _name_key(family_name(ref_author))
    return bool(key) and key in record_family_keys(author)


def author_display(author: WorkAuthor) -> str:
    return " ".join(part for part in (author.given, author.family) if part)


def venue_matches(left: str | None, right: str | None) -> bool:
    """Hai tên venue khớp (cùng cách so tiêu đề, ≥ `TITLE_ACCEPT`); thiếu một bên → False.

    Chỉ A1 dùng làm bằng chứng thêm khi tiêu đề lệch (`07a` §4.5.2 trường hợp 2): tên viết tắt
    và tên đầy đủ không khớp, nên chỉ có thể làm A1 từ chối, không bao giờ làm khớp sai.
    """
    return title_similarity(left, right) >= TITLE_ACCEPT


def _has_title(ref: ParsedReference) -> bool:
    return bool(ref.title and title_norm(ref.title))


def _token_count(title: str | None) -> int:
    return len(fold(title_norm(title)).split()) if title else 0


def _needs_corroboration(ref_title: str | None, cand_title: str) -> bool:
    """Tiêu đề ngắn ở một bên, hoặc ứng viên chỉ là phần nhỏ của tiêu đề trong bài."""
    ref_tokens = _token_count(ref_title)
    cand_tokens = _token_count(cand_title)
    return (
        ref_tokens < SHORT_TITLE_TOKENS
        or cand_tokens < SHORT_TITLE_TOKENS
        or cand_tokens < SUBSET_TOKEN_RATIO * ref_tokens
    )


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
    # Tiêu đề ngắn hoặc tiêu đề con: điểm tiêu đề cao chưa đủ, cần năm trùng hoặc cùng họ tác giả.
    title_corroborated = (
        not _needs_corroboration(ref.title, cand.title) or year_delta == 0 or same_author is True
    )
    accepted = (
        doi_equal
        or (title_score >= TITLE_ACCEPT and title_corroborated)
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
            Mismatch(field="authors", in_document=ref_author, found=author_display(cand_author))
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
        needs_review=doi_equal and _has_title(ref) and title_score < DOI_TITLE_CONFLICT,
    )


def pick_best(ref: ParsedReference, cands: Sequence[WorkRecord]) -> BestMatch | None:
    """Ứng viên được chấp nhận tốt nhất: ít trường lệch nhất, rồi điểm cao nhất (thứ tự nguồn
    giữ khi hòa); không có → None.
    """
    accepted = [(cand, result) for cand in cands if (result := match_candidate(ref, cand)).accepted]
    if not accepted:
        return None
    accepted.sort(key=lambda item: (len(item[1].mismatches), -item[1].score))
    record, best = accepted[0]
    close_call = any(
        (best.score - other.score) * 100 < CLOSE_DELTA
        and len(other.mismatches) <= len(best.mismatches)
        for _, other in accepted[1:]
    )
    return BestMatch(record=record, result=best, close_call=close_call)
