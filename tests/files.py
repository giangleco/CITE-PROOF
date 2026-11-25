"""Tệp mẫu tối thiểu cho test tải lên."""

import io
import zipfile

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def docx_bytes(*, with_document: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if with_document:
            archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


def stamped_pdf() -> bytes:
    """PDF ảnh quét có lớp chữ vụn: mỗi trang chỉ có dấu ứng dụng quét hoặc số trang."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    for stamp in ("Scanned with CamScanner", "2", "Scanned with CamScanner 3"):
        canvas.setFont("Helvetica", 9)
        canvas.drawString(400, 30, stamp)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def titled_pdf(title: str, *, pages: int = 1, metadata: bool = True) -> bytes:
    """PDF có chữ tiêu đề ở trang đầu (và tiêu đề trong metadata nếu `metadata`)."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    if metadata:
        canvas.setTitle(title)
    for number in range(pages):
        canvas.setFont("Helvetica", 14)
        canvas.drawString(72, 760, title if number == 0 else f"Trang {number + 1}")
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
