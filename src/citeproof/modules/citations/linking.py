"""Liên kết marker với mục tài liệu tham khảo (spec §7.4, TK-14; Review Focus #3).

**Số:** theo nhãn in trong danh mục (`parsed.label`) khi ít nhất một nửa số mục mang nhãn số,
ngược lại theo `order_index + 1` (mục GROBID không có nhãn). Số không có mục, hoặc nhãn trùng
(danh mục đánh số lại theo nhóm) → `None`.

**Tác giả-năm:** chỉ xét mục có tác giả đầu và năm trùng năm của marker; hậu tố a/b phải
trùng khi cả hai có (marker có hậu tố mà không mục nào cùng hậu tố thì xét mục không hậu
tố). So sánh sau khi gấp dấu, bỏ dấu câu và chữ hoa:
1. tên đầy đủ trùng tên tác giả đầu của đúng một mục;
2. không thì họ trùng (`family_name`, họ Việt đứng đầu) với đúng một mục, và phần tên còn lại
   không mâu thuẫn ("Nguyễn Văn An" hợp "Nguyen, V. A." nhưng không hợp "Nguyễn Thị Bình");
3. không có hoặc nhiều ứng viên → `None` (`REFERENCE_UNRESOLVED`): không bao giờ đoán.

`link` trả một phần tử cho mỗi số hay mỗi phần tác giả-năm của marker, theo thứ tự, kể cả
trùng (bỏ trùng là việc của bên gọi, preflight F41).
"""

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import LinkMethod
from citeproof.modules.citations.markers import AuthorYear, Marker
from citeproof.modules.references.normalize import family_name, fold, nfc

__all__ = ["LinkTarget", "ReferenceIndex", "make_target", "year_suffix"]

_NON_WORD = re.compile(r"[\W_]+")
_NUMERIC_LABEL = re.compile(r"[0-9]{1,4}")


@dataclass(frozen=True)
class LinkTarget:
    entry_id: UUID
    order_index: int
    label: str | None
    authors: tuple[str, ...]
    year: int | None
    year_suffix: str | None


def year_suffix(raw_text: str, year: int | None) -> str | None:
    """Hậu tố a/b dính sau lần xuất hiện đầu tiên của năm trong chuỗi gốc ("(2019a)")."""
    if year is None:
        return None
    match = re.search(rf"(?<!\d){year}(?!\d)", raw_text)
    if match is None:
        return None
    after = raw_text[match.end() : match.end() + 2]
    if after[:1].isascii() and after[:1].islower() and not after[1:2].isalpha():
        return after[0]
    return None


def make_target(
    entry_id: UUID, order_index: int, parsed: Mapping[str, Any], raw_text: str
) -> LinkTarget:
    """`LinkTarget` từ một dòng `reference_entries` (`parsed` hỏng được coi như rỗng)."""
    try:
        fields = ParsedReference.model_validate(parsed)
    except ValidationError:
        fields = ParsedReference()
    return LinkTarget(
        entry_id=entry_id,
        order_index=order_index,
        label=fields.label,
        authors=tuple(fields.authors),
        year=fields.year,
        year_suffix=year_suffix(raw_text, fields.year),
    )


def _key(text: str) -> str:
    """Chuỗi so sánh tên: NFC, gấp dấu, chữ thường, dấu câu thành khoảng trắng."""
    return " ".join(_NON_WORD.sub(" ", fold(nfc(text))).split())


@dataclass(frozen=True)
class _Name:
    full: str
    family: str
    given: tuple[str, ...]


def _name(author: str) -> _Name:
    full = _key(author).split()
    family = _key(family_name(author)).split()
    given = full
    for start in range(len(full) - len(family) + 1):
        if family and full[start : start + len(family)] == family:
            given = full[:start] + full[start + len(family) :]
            break
    return _Name(full=" ".join(full), family=" ".join(family), given=tuple(given))


def _given_compatible(cited: Sequence[str], listed: Sequence[str]) -> bool:
    """Phần tên ngoài họ không mâu thuẫn: thiếu một bên, hoặc từng từ trùng (chữ viết tắt so
    chữ cái đầu).
    """
    if not cited or not listed:
        return True
    if len(cited) != len(listed):
        return False
    return all(
        a == b if len(a) > 1 and len(b) > 1 else a[:1] == b[:1]
        for a, b in zip(cited, listed, strict=True)
    )


@dataclass(frozen=True)
class _Person:
    target: LinkTarget
    name: _Name


class ReferenceIndex:
    """Tra mục tài liệu tham khảo của một tài liệu theo số hoặc theo tác giả-năm."""

    def __init__(self, targets: Sequence[LinkTarget]) -> None:
        labelled = [
            (int(target.label), target.entry_id)
            for target in targets
            if target.label is not None and _NUMERIC_LABEL.fullmatch(target.label.strip())
        ]
        # Danh mục "có nhãn" khi ít nhất một nửa số mục mang nhãn số; nhãn lạc trong danh mục
        # APA không làm mất cách đánh số theo thứ tự.
        numbered = (
            labelled
            if 2 * len(labelled) >= len(targets) > 0
            else [(target.order_index + 1, target.entry_id) for target in targets]
        )
        by_number: defaultdict[int, list[UUID]] = defaultdict(list)
        for number, entry_id in numbered:
            by_number[number].append(entry_id)
        self._by_number: dict[int, UUID | None] = {
            number: ids[0] if len(ids) == 1 else None for number, ids in by_number.items()
        }
        self._people = [
            _Person(target, _name(target.authors[0]))
            for target in targets
            if target.authors and target.year is not None and target.authors[0].strip()
        ]

    def link(self, marker: Marker) -> list[UUID | None]:
        if marker.kind is LinkMethod.NUMERIC:
            return [self._by_number.get(number) for number in marker.numbers]
        return [self._author_year(cite) for cite in marker.cites]

    def _same_year(self, cite: AuthorYear) -> list[_Person]:
        people = [p for p in self._people if p.target.year == cite.year]
        if cite.suffix is None:
            return people
        exact = [p for p in people if p.target.year_suffix == cite.suffix]
        return exact or [p for p in people if p.target.year_suffix is None]

    def _author_year(self, cite: AuthorYear) -> UUID | None:
        name = _name(cite.name)
        people = self._same_year(cite)
        full = [p for p in people if p.name.full == name.full]
        if full:
            return full[0].target.entry_id if len(full) == 1 else None
        family = [
            p
            for p in people
            if p.name.family == name.full
            or (p.name.family == name.family and _given_compatible(name.given, p.name.given))
        ]
        return family[0].target.entry_id if len(family) == 1 else None
