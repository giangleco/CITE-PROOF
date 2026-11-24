"""TEI của GROBID → `ParsedDocument` và `ParsedReference` (spec §7.1, QĐ-03).

- Đoạn theo thứ tự: abstract (mục "Abstract"), thân bài, rồi các phần `<back>` không phải
  danh mục (phụ lục, lời cảm ơn). `<head>` mở mục mới (kèm số mục ở thuộc tính `n`) và
  thành đoạn `kind="heading"`; `<head>` nằm trong abstract cũng đổi mục, vì GROBID hay
  gộp phần mở đầu của bài vào abstract.
- Câu `<s>` nối bằng một khoảng trắng; khoảng trắng liên tiếp gộp làm một.
- Trang lấy từ hộp đầu tiên của thuộc tính `coords` ("trang,x,y,rộng,cao;…") trên phần tử
  hoặc phần tử con đầu tiên có `coords`.
- `<figure>` chỉ lấy chú thích (`head` + `figDesc`) làm `kind="caption"`, vùng TABLE khi
  `type="table"`, còn lại FIGURE; chú thích chân trang `<note place="foot">` vùng OTHER.
- `<listBibl>` trong `<div type="references">` → `references_raw`: `raw` từ
  `note[@type="raw_reference"]` (GROBID đã bỏ nhãn `[n]`), `parsed` từ `biblStruct`.

XML parse bằng `defusedxml`; có DTD hoặc entity → `ParseError(code="TEI_REJECTED")`.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element
from xml.etree.ElementTree import ParseError as XmlParseError

from defusedxml import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml.ElementTree import fromstring

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import ChunkRegion
from citeproof.core.errors import ParseError
from citeproof.parsing.model import ParagraphDraft, ParagraphKind, ParsedDocument, RawReference

TEI_NS = "http://www.tei-c.org/ns/1.0"
ABSTRACT_SECTION = "Abstract"
_T = f"{{{TEI_NS}}}"
_WHITESPACE = re.compile(r"\s+")
_YEAR = re.compile(r"\b(\d{4})\b")
# Phần tử khối: thêm khoảng trắng sau mỗi phần tử để câu/ô không dính vào nhau.
_BLOCK_TAGS = frozenset({"s", "p", "head", "lb", "item", "cell", "row", "figDesc", "label"})


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _parse_xml(tei: str) -> Element:
    try:
        return fromstring(tei, forbid_dtd=True)
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise ParseError(
            "Kết quả GROBID có khai báo DTD hoặc entity nên bị từ chối.", code="TEI_REJECTED"
        ) from exc
    except XmlParseError as exc:
        raise ParseError("Kết quả GROBID không phải XML hợp lệ.", code="TEI_INVALID") from exc


def _collect(node: Element, parts: list[str]) -> None:
    if node.text:
        parts.append(node.text)
    for child in node:
        _collect(child, parts)
        if _local(child.tag) in _BLOCK_TAGS:
            parts.append(" ")
        if child.tail:
            parts.append(child.tail)


def _text(node: Element | None) -> str:
    if node is None:
        return ""
    parts: list[str] = []
    _collect(node, parts)
    return _WHITESPACE.sub(" ", "".join(parts)).strip()


def _page(node: Element) -> int | None:
    """Số trang từ hộp đầu tiên của `coords` trên phần tử hoặc phần tử con đầu tiên có nó."""
    for item in node.iter():
        coords = item.get("coords")
        if coords:
            first = coords.split(";", 1)[0].split(",", 1)[0].strip()
            return int(first) if first.isdigit() else None
    return None


def _heading_text(head: Element) -> str:
    text = _text(head)
    number = (head.get("n") or "").strip()
    return f"{number} {text}".strip() if number and text else text


@dataclass
class _Walker:
    """Duyệt các phần tử khối theo thứ tự tài liệu, giữ mục hiện tại."""

    section: str | None = None
    drafts: list[ParagraphDraft] = field(default_factory=list)

    def add(
        self,
        text: str,
        page: int | None,
        *,
        kind: ParagraphKind = "body",
        region: ChunkRegion = ChunkRegion.BODY,
    ) -> None:
        if text:
            self.drafts.append(
                ParagraphDraft(text=text, section=self.section, page=page, region=region, kind=kind)
            )

    def walk(self, node: Element) -> None:
        for child in node:
            tag = _local(child.tag)
            if tag == "div":
                if child.get("type") != "references":
                    self.walk(child)
            elif tag == "head":
                heading = _heading_text(child)
                if heading:
                    self.section = heading
                    self.add(heading, _page(child), kind="heading")
            elif tag == "p":
                self.add(_text(child), _page(child))
            elif tag == "list":
                for item in child.iter(f"{_T}item"):
                    self.add(_text(item), _page(item))
            elif tag == "figure":
                self._figure(child)
            elif tag == "note" and child.get("place") == "foot":
                self.add(_text(child), _page(child), region=ChunkRegion.OTHER)

    def _figure(self, figure: Element) -> None:
        head = _text(figure.find(f"{_T}head"))
        desc = _text(figure.find(f"{_T}figDesc"))
        caption = f"{head} {desc}".strip()
        region = ChunkRegion.TABLE if figure.get("type") == "table" else ChunkRegion.FIGURE
        self.add(caption, _page(figure), kind="caption", region=region)


def _first(node: Element | None, path: str) -> Element | None:
    return None if node is None else node.find(path)


def _title(node: Element | None) -> str | None:
    if node is None:
        return None
    title = node.find(f"{_T}title[@type='main']")
    if title is None:
        title = node.find(f"{_T}title")
    return _text(title) or None


def _author_name(author: Element) -> str:
    person = author.find(f"{_T}persName")
    if person is None:
        return _text(author.find(f"{_T}orgName"))
    # Giữ thứ tự đọc trong bài (tên đệm trước họ với tên Tây; họ trước với tên Việt bị
    # GROBID tách nhầm) để S2 tự lấy họ bằng quy tắc của nó.
    names = [_text(forename) for forename in person.findall(f"{_T}forename")]
    names.append(_text(person.find(f"{_T}surname")))
    return " ".join(name for name in names if name)


def _authors(analytic: Element | None, monogr: Element | None) -> list[str]:
    for node in (analytic, monogr):
        if node is None:
            continue
        names = [_author_name(author) for author in node.findall(f"{_T}author")]
        names = [name for name in names if name]
        if names:
            return names
    return []


def _year(bibl: Element) -> int | None:
    dates = list(bibl.iter(f"{_T}date"))
    dates.sort(key=lambda date: date.get("type") != "published")
    for date in dates:
        match = _YEAR.search(date.get("when") or "") or _YEAR.search(_text(date))
        if match:
            return int(match.group(1))
    return None


def _idno(bibl: Element, kind: str) -> str | None:
    for idno in bibl.iter(f"{_T}idno"):
        if (idno.get("type") or "").upper() == kind:
            value = _text(idno)
            if value:
                return value
    return None


def _url(bibl: Element) -> str | None:
    for ptr in bibl.iter(f"{_T}ptr"):
        target = (ptr.get("target") or "").strip()
        if target.startswith(("http://", "https://")):
            return target
    return None


def _reference_fields(bibl: Element) -> ParsedReference:
    """Trường của một `biblStruct`; DOI giữ nguyên cách viết (S2 chuẩn hóa)."""
    analytic = bibl.find(f"{_T}analytic")
    monogr = bibl.find(f"{_T}monogr")
    title = _title(analytic)
    venue: str | None = None
    if title is None:
        title = _title(monogr)
    else:
        venue = _title(monogr) or (_text(_first(monogr, f"{_T}meeting")) or None)
    return ParsedReference(
        title=title,
        authors=_authors(analytic, monogr),
        year=_year(bibl),
        venue=venue,
        doi=_idno(bibl, "DOI"),
        isbn=_idno(bibl, "ISBN"),
        url=_url(bibl),
    )


def _raw_reference(bibl: Element) -> str:
    for note in bibl.findall(f"{_T}note"):
        if note.get("type") == "raw_reference":
            return _text(note)
    return _text(bibl)


def _bibl_structs(root: Element) -> Iterator[Element]:
    for div in root.iter(f"{_T}div"):
        if div.get("type") == "references":
            yield from div.iter(f"{_T}biblStruct")


def parse_fulltext_tei(tei: str, *, page_count: int | None) -> ParsedDocument:
    """TEI `processFulltextDocument` → `ParsedDocument(source="grobid")`.

    `page_count` None thì đếm `<surface>` trong `<facsimile>` (nếu có).
    """
    root = _parse_xml(tei)
    walker = _Walker()
    for abstract in root.iter(f"{_T}abstract"):
        walker.section = ABSTRACT_SECTION
        walker.walk(abstract)
    walker.section = None
    text = root.find(f"{_T}text")
    if text is not None:
        for part in ("front", "body", "back"):
            node = text.find(f"{_T}{part}")
            if node is not None:
                walker.walk(node)
    references = [
        RawReference(raw=_raw_reference(bibl), page=_page(bibl), parsed=_reference_fields(bibl))
        for bibl in _bibl_structs(root)
    ]
    if page_count is None:
        surfaces = list(root.iter(f"{_T}surface"))
        page_count = len(surfaces) or None
    return ParsedDocument.build(
        source="grobid", drafts=walker.drafts, page_count=page_count, references_raw=references
    )


def parse_citation_list_tei(tei: str) -> list[ParsedReference]:
    """TEI `processCitationList` → trường của từng chuỗi, cùng thứ tự đầu vào."""
    root = _parse_xml(tei)
    return [_reference_fields(bibl) for bibl in root.iter(f"{_T}biblStruct")]
