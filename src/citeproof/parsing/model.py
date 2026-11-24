"""Mô hình văn bản đã parse (`parsed/1`, spec §13), dùng chung cho S1 và S6 (QĐ-34).

`ParsedDocument.text()` là các đoạn nối bằng `"\\n\\n"`; mọi `char_start`/`char_end`
tính trên chuỗi này, nên `doc.text()[p.char_start:p.char_end] == p.text`.
"""

import unicodedata
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import ChunkRegion, Language
from citeproof.parsing.language import detect_language, document_language

SchemaVersion = Literal["parsed/1"]
SCHEMA_VERSION: SchemaVersion = "parsed/1"
PARAGRAPH_SEPARATOR = "\n\n"

ParagraphKind = Literal["body", "heading", "caption"]
ParsedSource = Literal["text", "docx", "grobid", "pdfium"]


class ParagraphDraft(BaseModel):
    """Đoạn do parser tạo, chưa chuẩn hóa và chưa có vị trí."""

    text: str
    section: str | None
    page: int | None
    region: ChunkRegion = ChunkRegion.BODY
    kind: ParagraphKind = "body"


class Paragraph(ParagraphDraft):
    index: int
    char_start: int
    char_end: int
    language: Language


class RawReference(BaseModel):
    """Chuỗi tài liệu tham khảo thô (không kèm nhãn `[n]`/`n.`), có thể đã có trường parse."""

    raw: str
    label: str | None = None
    page: int | None = None
    parsed: ParsedReference | None = None


def _clean(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


class ParsedDocument(BaseModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    source: ParsedSource
    page_count: int | None
    language: Language
    paragraphs: list[Paragraph]
    references_raw: list[RawReference] | None

    def text(self) -> str:
        return PARAGRAPH_SEPARATOR.join(p.text for p in self.paragraphs)

    @classmethod
    def build(
        cls,
        *,
        source: ParsedSource,
        drafts: Sequence[ParagraphDraft],
        page_count: int | None,
        references_raw: Sequence[RawReference] | None = None,
    ) -> Self:
        """Chuẩn hóa NFC, bỏ đoạn rỗng, tính vị trí ký tự và ngôn ngữ từng đoạn."""
        paragraphs: list[Paragraph] = []
        offset = 0
        for draft in drafts:
            text = _clean(draft.text)
            if not text:
                continue
            if paragraphs:
                offset += len(PARAGRAPH_SEPARATOR)
            section = _clean(draft.section) if draft.section is not None else None
            paragraphs.append(
                Paragraph(
                    text=text,
                    section=section or None,
                    page=draft.page,
                    region=draft.region,
                    kind=draft.kind,
                    index=len(paragraphs),
                    char_start=offset,
                    char_end=offset + len(text),
                    language=detect_language(text),
                )
            )
            offset += len(text)
        references: list[RawReference] | None = None
        if references_raw is not None:
            references = []
            for ref in references_raw:
                raw = _clean(ref.raw)
                if raw:
                    references.append(ref.model_copy(update={"raw": raw}))
        return cls(
            schema_version=SCHEMA_VERSION,
            source=source,
            page_count=page_count,
            language=document_language(paragraphs),
            paragraphs=paragraphs,
            references_raw=references,
        )
