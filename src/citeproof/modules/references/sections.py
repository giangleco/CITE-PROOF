"""Khối danh mục tài liệu tham khảo trong tài liệu đã parse (spec §7.2).

Nguồn chuỗi thô của S2: `references_raw` khi S1 đã tách sẵn (văn bản nhập tay, GROBID,
danh mục pypdfium2 khi GROBID không tách được); ngược lại là khối sau tiêu đề cuối cùng khớp
`REFERENCE_HEADING` (gấp dấu, không phân biệt hoa thường) tới tiêu đề "Phụ lục"/"Appendix".
Tiêu đề nằm ở mục lục bị bỏ qua vì chỉ lấy tiêu đề cuối cùng. S4 dùng
`reference_paragraph_indices` để không tách claim trong khối này.

Tách mục dùng chung bộ quy tắc với S1 (`citeproof.parsing.references`): mỗi dòng của đoạn
là một dòng; ranh giới đoạn là ranh giới dòng trống, trừ khi hai đoạn PDF nằm ở hai trang
liền nhau (mục bị ngắt trang). Đoạn tiêu đề trong khối (style Heading) và tiêu đề nhóm
("Tiếng Việt", "Tài liệu tiếng nước ngoài") bị bỏ và đặt lại thứ tự nhãn. Chuỗi đã lưu chỉ
có nhãn (`[3]`) bị bỏ.
"""

from collections.abc import Sequence

from citeproof.core.enums import ChunkRegion
from citeproof.parsing.model import Paragraph, ParsedDocument, RawReference
from citeproof.parsing.references import (
    REFERENCE_HEADING,
    ReferenceLine,
    has_reference_text,
    is_appendix_heading,
    is_reference_heading,
    join_unlabelled,
    split_reference_lines,
)

__all__ = [
    "REFERENCE_HEADING",
    "document_references",
    "find_reference_block",
    "reference_paragraph_indices",
    "split_entries",
]


def _single_line(paragraph: Paragraph) -> bool:
    return "\n" not in paragraph.text


def _is_heading_paragraph(paragraph: Paragraph) -> bool:
    return (
        paragraph.region is ChunkRegion.BODY
        and paragraph.kind != "caption"
        and _single_line(paragraph)
        and is_reference_heading(paragraph.text)
    )


def _heading_index(doc: ParsedDocument) -> int | None:
    return max(
        (p.index for p in doc.paragraphs if _is_heading_paragraph(p)),
        default=None,
    )


def _block(doc: ParsedDocument, heading: int) -> list[Paragraph]:
    block: list[Paragraph] = []
    for paragraph in doc.paragraphs[heading + 1 :]:
        if _single_line(paragraph) and is_appendix_heading(paragraph.text):
            break
        block.append(paragraph)
    return block


def find_reference_block(doc: ParsedDocument) -> list[Paragraph]:
    """Đoạn sau tiêu đề danh mục cuối cùng, tới tiêu đề phụ lục; `[]` khi không có tiêu đề."""
    heading = _heading_index(doc)
    return [] if heading is None else _block(doc, heading)


def reference_paragraph_indices(doc: ParsedDocument) -> frozenset[int]:
    """Chỉ số đoạn tiêu đề danh mục và khối danh mục (S4 bỏ qua khi tách claim)."""
    heading = _heading_index(doc)
    if heading is None:
        return frozenset()
    return frozenset({heading, *(p.index for p in _block(doc, heading))})


def _page_continues(previous: Paragraph, current: Paragraph) -> bool:
    return previous.page is not None and current.page is not None and current.page != previous.page


def split_entries(paragraphs: Sequence[Paragraph]) -> list[RawReference]:
    """Tách các đoạn của khối danh mục thành mục (nhãn `[n]`/`n.`, dòng ngắt được gộp)."""
    lines: list[ReferenceLine] = []
    group_breaks: set[int] = set()
    previous: Paragraph | None = None
    for paragraph in paragraphs:
        if paragraph.kind == "caption":
            continue
        if paragraph.kind == "heading":
            # Đoạn style Heading trong khối là tiêu đề nhóm: bỏ chữ, đánh số lại.
            group_breaks.add(len(lines))
            lines.append((paragraph.page, ""))
            previous = None
            continue
        if previous is not None and not _page_continues(previous, paragraph):
            lines.append((paragraph.page, ""))
        lines.extend((paragraph.page, line) for line in paragraph.text.split("\n"))
        previous = paragraph
    return split_reference_lines(lines, group_breaks=group_breaks)


def document_references(doc: ParsedDocument) -> list[RawReference]:
    """Chuỗi tài liệu tham khảo thô của tài liệu, theo thứ tự (`[]` khi không có danh mục)."""
    if doc.references_raw:
        stored = [ref for ref in doc.references_raw if has_reference_text(ref.raw)]
        return join_unlabelled(stored)
    return split_entries(find_reference_block(doc))
