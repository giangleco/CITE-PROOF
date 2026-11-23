"""Tệp mẫu tối thiểu cho test tải lên."""

import io
import zipfile

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def docx_bytes(*, with_document: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if with_document:
            archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()
