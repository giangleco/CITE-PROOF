"""Sinh đầu vào của bộ golden parsing (T-208); chỉ chạy khi cần tạo lại fixture, CI không chạy.

    uv run python scripts/make_golden_fixtures.py [--out tests/fixtures/golden]

PDF sinh bằng reportlab với font DejaVu Sans của hệ thống (có tiếng Việt), DOCX bằng
python-docx. Đầu ra tất định: ngày tạo và ID tài liệu cố định, zip DOCX có mốc thời gian cố
định. Mọi tên người của luận văn nội địa đều hư cấu; tài liệu nổi tiếng dùng thông tin thư
mục công khai. `RAW_REFERENCES` là nguồn chuỗi tham khảo thô cho `record_grobid_fixtures.py`.
"""

import argparse
import io
import json
import re
import sys
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "golden"
FIXED_DATE = (2026, 1, 1, 0, 0, 0)
PDF_PASSWORD = "citeproof-golden"

# --- Bảng mã TCVN3 (ABC): ký tự Unicode -> byte, đọc lại bằng Latin-1 thành "mojibake" ------
_TCVN3_LOWER = {
    "à": 0xB5, "ả": 0xB6, "ã": 0xB7, "á": 0xB8, "ạ": 0xB9,
    "ă": 0xA8, "ằ": 0xBB, "ẳ": 0xBC, "ẵ": 0xBD, "ắ": 0xBE, "ặ": 0xC6,
    "â": 0xA9, "ầ": 0xC7, "ẩ": 0xC8, "ẫ": 0xC9, "ấ": 0xCA, "ậ": 0xCB,
    "đ": 0xAE,
    "è": 0xCC, "ẻ": 0xCE, "ẽ": 0xCF, "é": 0xD0, "ẹ": 0xD1,
    "ê": 0xAA, "ề": 0xD2, "ể": 0xD3, "ễ": 0xD4, "ế": 0xD5, "ệ": 0xD6,
    "ì": 0xD7, "ỉ": 0xD8, "ĩ": 0xDC, "í": 0xDD, "ị": 0xDE,
    "ò": 0xDF, "ỏ": 0xE1, "õ": 0xE2, "ó": 0xE3, "ọ": 0xE4,
    "ô": 0xAB, "ồ": 0xE5, "ổ": 0xE6, "ỗ": 0xE7, "ố": 0xE8, "ộ": 0xE9,
    "ơ": 0xAC, "ờ": 0xEA, "ở": 0xEB, "ỡ": 0xEC, "ớ": 0xED, "ợ": 0xEE,
    "ù": 0xEF, "ủ": 0xF1, "ũ": 0xF2, "ú": 0xF3, "ụ": 0xF4,
    "ư": 0xAD, "ừ": 0xF5, "ử": 0xF6, "ữ": 0xF7, "ứ": 0xF8, "ự": 0xF9,
    "ỳ": 0xFA, "ỷ": 0xFB, "ỹ": 0xFC, "ý": 0xFD, "ỵ": 0xFE,
}  # fmt: skip
_TCVN3_UPPER = {"Ă": 0xA1, "Â": 0xA2, "Ê": 0xA3, "Ô": 0xA4, "Ơ": 0xA5, "Ư": 0xA6, "Đ": 0xA7}
TCVN3 = {**_TCVN3_LOWER, **_TCVN3_UPPER}


def to_tcvn3_mojibake(text: str) -> str:
    """Mã hóa câu tiếng Việt sang TCVN3 rồi đọc như Latin-1, đúng thứ tệp .VnTime bị đọc sai."""
    out: list[str] = []
    for char in text:
        if char in TCVN3:
            out.append(chr(TCVN3[char]))
        elif ord(char) < 0x80:
            out.append(char)
        else:
            raise SystemExit(f"Ký tự {char!r} chưa có trong bảng TCVN3 của script")
    return "".join(out)


# --- Nội dung ---------------------------------------------------------------------------------
REF_AN = (
    'Nguyễn Văn An (2020), "Phân loại văn bản tiếng Việt bằng mạng nơ-ron tích chập", '
    "Tạp chí Khoa học và Công nghệ Mẫu, 12(3), tr. 45-58."
)
REF_BICH = (
    "Trần Thị Bích (2021), Nhận dạng thực thể có tên trong văn bản tiếng Việt, "
    "Luận văn thạc sĩ, Trường Đại học Mẫu, Hà Nội."
)
REF_VASWANI = (
    'Vaswani A., Shazeer N., Parmar N., et al. (2017), "Attention is all you need", '
    "arXiv preprint arXiv:1706.03762, doi:10.48550/arXiv.1706.03762."
)
REF_CUONG = (
    "Lê Hoàng Cường (2019), Giáo trình xử lý ngôn ngữ tự nhiên, NXB Giáo dục Việt Nam, Hà Nội."
)
REF_DEVLIN_VI = (
    'Devlin J., Chang M.-W., Lee K., Toutanova K. (2019), "BERT: Pre-training of deep '
    'bidirectional transformers for language understanding", Proceedings of NAACL-HLT, '
    "tr. 4171-4186, doi:10.18653/v1/N19-1423."
)
REF_HE_VI = (
    'He K., Zhang X., Ren S., Sun J. (2016), "Deep residual learning for image recognition", '
    "Proceedings of IEEE CVPR, tr. 770-778, doi:10.1109/CVPR.2016.90."
)
REF_DUC = (
    "Phạm Minh Đức (2018), Xây dựng hệ thống hỏi đáp tiếng Việt, Luận văn thạc sĩ, "
    "Trường Đại học Mẫu, TP. Hồ Chí Minh."
)

VI_THESIS_REFS = [f"[{n}] {t}" for n, t in enumerate([REF_AN, REF_BICH, REF_VASWANI, REF_CUONG], 1)]
VI_TEXT_REFS = [f"[{n}] {t}" for n, t in enumerate([REF_DEVLIN_VI, REF_HE_VI, REF_DUC], 1)]

# multiline_refs.docx: mỗi mục tách thành nhiều đoạn, dòng tiếp theo không có nhãn.
MULTILINE_ENTRIES = [
    [
        '[1] Nguyễn Văn An (2020), "Phân loại văn bản tiếng Việt bằng',
        'mạng nơ-ron tích chập", Tạp chí Khoa học và Công nghệ Mẫu,',
        "12(3), tr. 45-58.",
    ],
    [
        "[2] Trần Thị Bích (2021), Nhận dạng thực thể có tên trong văn bản",
        "tiếng Việt, Luận văn thạc sĩ, Trường Đại học Mẫu, Hà Nội.",
    ],
    [
        '[3] Vaswani A., Shazeer N., Parmar N., et al. (2017), "Attention is all you need",',
        "arXiv preprint arXiv:1706.03762,",
        "doi:10.48550/arXiv.1706.03762.",
    ],
    [
        "[4] Lê Hoàng Cường (2019), Giáo trình xử lý ngôn ngữ tự nhiên,",
        "NXB Giáo dục Việt Nam, Hà Nội.",
    ],
]  # fmt: skip

APA_REFS = [
    "Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K. (2019). BERT: Pre-training of deep "
    "bidirectional transformers for language understanding. In Proceedings of the 2019 "
    "Conference of the North American Chapter of the Association for Computational "
    "Linguistics (pp. 4171-4186). https://doi.org/10.18653/v1/N19-1423",
    "He, K., Zhang, X., Ren, S., & Sun, J. (2016). Deep residual learning for image "
    "recognition. In Proceedings of the IEEE Conference on Computer Vision and Pattern "
    "Recognition (pp. 770-778). https://doi.org/10.1109/CVPR.2016.90",
    "Hochreiter, S., & Schmidhuber, J. (1997). Long short-term memory. Neural Computation, "
    "9(8), 1735-1780. https://doi.org/10.1162/neco.1997.9.8.1735",
    "Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A. N., Kaiser, L., "
    "& Polosukhin, I. (2017). Attention is all you need. arXiv preprint arXiv:1706.03762. "
    "https://doi.org/10.48550/arXiv.1706.03762",
]

MIXED_APA_REFS = [
    APA_REFS[0],
    "Nguyễn Văn An (2020). Phân loại văn bản tiếng Việt bằng mạng nơ-ron tích chập. "
    "Tạp chí Khoa học và Công nghệ Mẫu, 12(3), 45-58.",
    APA_REFS[3],
]

IEEE_REFS = [
    'A. Vaswani et al., "Attention is all you need," arXiv:1706.03762, 2017, '
    "doi: 10.48550/arXiv.1706.03762.",
    'J. Devlin, M.-W. Chang, K. Lee, and K. Toutanova, "BERT: Pre-training of deep '
    'bidirectional transformers for language understanding," in Proc. NAACL-HLT, 2019, '
    "pp. 4171-4186, doi: 10.18653/v1/N19-1423.",
    'K. He, X. Zhang, S. Ren, and J. Sun, "Deep residual learning for image recognition," '
    "in Proc. IEEE CVPR, 2016, pp. 770-778, doi: 10.1109/CVPR.2016.90.",
    'S. Hochreiter and J. Schmidhuber, "Long short-term memory," Neural Comput., vol. 9, '
    "no. 8, pp. 1735-1780, 1997, doi: 10.1162/neco.1997.9.8.1735.",
]

TCVN3_PARAGRAPHS = [
    "Hệ thống kiểm tra trích dẫn tự động cho luận văn tiếng Việt.",
    "Nhiều tài liệu cũ vẫn dùng bảng mã TCVN3 với phông chữ .VnTime nên văn bản mở ra bị hỏng dấu.",
    "Hệ thống cần phát hiện tình trạng này và cảnh báo người dùng thay vì đoán nội dung.",
    "Việc chuyển đổi bảng mã nằm ngoài phạm vi của bản thử nghiệm.",
]

_LABEL = re.compile(r"^\[\d+\]\s*")


def _unlabelled(entries: list[str]) -> list[str]:
    return [_LABEL.sub("", entry) for entry in entries]


# Chuỗi tham khảo thô (đã bỏ nhãn `[n]`, như `RawReference.raw`) cho recorder GROBID; khóa là
# tên fixture DOCX/JSON.
RAW_REFERENCES: dict[str, list[str]] = {
    "vi_thesis_numeric": _unlabelled(VI_THESIS_REFS),
    "en_paper_author_year": APA_REFS,
    "vi_tcvn3_legacy": [to_tcvn3_mojibake(TCVN3_PARAGRAPHS[0])],
    "vi_text_input": _unlabelled(VI_TEXT_REFS),
    "multiline_refs": _unlabelled([" ".join(e) for e in MULTILINE_ENTRIES]),
}


# --- Công cụ DOCX -----------------------------------------------------------------------------
def _freeze_docx(doc: DocxDocument, path: Path) -> None:
    props = doc.core_properties
    props.author = "CiteProof golden"
    props.last_modified_by = "CiteProof golden"
    props.created = props.modified = datetime(*FIXED_DATE)
    props.title = path.stem
    buffer = io.BytesIO()
    doc.save(buffer)
    with (
        zipfile.ZipFile(buffer) as src,
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for item in src.infolist():
            info = zipfile.ZipInfo(item.filename, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = item.external_attr
            dst.writestr(info, src.read(item.filename))


def make_vi_thesis_numeric(path: Path) -> None:
    doc = Document()
    doc.add_heading("Chương 1. Giới thiệu", level=1)
    doc.add_paragraph(
        "Kiểm tra trích dẫn là bước quan trọng trong quá trình hoàn thiện luận văn. "
        "Các mô hình học sâu cho văn bản tiếng Việt đã đạt kết quả tốt trên nhiều bài toán "
        "phân loại [1]. Nhận dạng thực thể có tên trong tiếng Việt vẫn còn nhiều thách thức "
        "về dữ liệu gán nhãn [2]."
    )
    doc.add_heading("Chương 2. Cơ sở lý thuyết", level=1)
    doc.add_paragraph(
        "Kiến trúc Transformer dựa hoàn toàn trên cơ chế tự chú ý và cho kết quả vượt trội "
        "trong dịch máy [3]. Các giáo trình xử lý ngôn ngữ tự nhiên trình bày nền tảng này "
        "một cách hệ thống [4]. Một số nghiên cứu đã đề xuất cải tiến cho cả hai hướng tiếp "
        "cận [1, 3]. Một số tác giả khác cũng có kết luận tương tự [5]."
    )
    doc.add_heading("Tài liệu tham khảo", level=1)
    for entry in VI_THESIS_REFS:
        doc.add_paragraph(entry)
    _freeze_docx(doc, path)


def make_en_paper_author_year(path: Path) -> None:
    doc = Document()
    doc.add_heading("A Short Survey of Sequence Models", level=0)
    doc.add_heading("1. Introduction", level=1)
    doc.add_paragraph(
        "Sequence transduction models were long dominated by recurrent architectures "
        "(Hochreiter & Schmidhuber, 1997). The Transformer replaced recurrence with "
        "self-attention and set a new state of the art in machine translation "
        "(Vaswani et al., 2017). Pre-trained language models such as BERT further improved "
        "many benchmarks (Devlin et al., 2019)."
    )
    doc.add_heading("2. Related Work", level=1)
    doc.add_paragraph(
        "Deep residual networks made very deep models trainable (He et al., 2016; "
        "Vaswani et al., 2017). Earlier work has also argued otherwise (Smith, 2010)."
    )
    doc.add_heading("References", level=1)
    for entry in APA_REFS:
        doc.add_paragraph(entry)
    _freeze_docx(doc, path)


def make_vi_tcvn3_legacy(path: Path) -> None:
    doc = Document()
    for text in TCVN3_PARAGRAPHS:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(to_tcvn3_mojibake(text))
        run.font.name = ".VnTime"
        fonts = run._element.get_or_add_rPr().rFonts
        for attr in ("w:ascii", "w:hAnsi", "w:cs"):
            fonts.set(qn(attr), ".VnTime")
    _freeze_docx(doc, path)


def make_multiline_refs(path: Path) -> None:
    doc = Document()
    doc.add_paragraph(
        "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi [1]. Nhận dạng thực thể "
        "có tên vẫn là bài toán khó [2]. Cơ chế tự chú ý là nền tảng của các mô hình "
        "hiện đại [3]. Giáo trình trình bày các khái niệm cơ bản của lĩnh vực [4]."
    )
    doc.add_heading("Tài liệu tham khảo", level=1)
    for lines in MULTILINE_ENTRIES:
        for line in lines:
            doc.add_paragraph(line)
    _freeze_docx(doc, path)


def make_vi_text_input(path: Path) -> None:
    body = (
        "Trích dẫn trong khóa luận cần đối chiếu được với nguồn gốc. BERT là mô hình ngôn "
        "ngữ huấn luyện trước hai chiều cho nhiều bài toán xử lý ngôn ngữ [1].\n\n"
        "Mạng residual cho phép huấn luyện mạng rất sâu mà không bị suy giảm độ chính xác "
        "[2]. Hai hướng này thường được kết hợp trong các hệ thống thực tế [1, 2]. Hệ thống "
        "hỏi đáp tiếng Việt vẫn thiếu dữ liệu huấn luyện [3]."
    )
    payload = {"body": body, "references": VI_TEXT_REFS}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- Công cụ PDF ------------------------------------------------------------------------------
def _register_fonts() -> None:
    regular = FONT_DIR / "DejaVuSans.ttf"
    bold = FONT_DIR / "DejaVuSans-Bold.ttf"
    for font in (regular, bold):
        if not font.exists():
            raise SystemExit(
                f"Thiếu font {font}. Cài gói fonts-dejavu-core (apt install fonts-dejavu-core)."
            )
    registerFont(TTFont("DejaVu", str(regular)))
    registerFont(TTFont("DejaVu-Bold", str(bold)))
    pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")


def _styles() -> dict[str, ParagraphStyle]:
    body = ParagraphStyle("body", fontName="DejaVu", fontSize=10.5, leading=15, spaceAfter=8)
    return {
        "body": body,
        "title": ParagraphStyle("title", parent=body, fontName="DejaVu-Bold", fontSize=16,
                                leading=22, spaceAfter=14),
        "heading": ParagraphStyle("heading", parent=body, fontName="DejaVu-Bold", fontSize=12,
                                  leading=17, spaceBefore=8),
        "caption": ParagraphStyle("caption", parent=body, fontSize=9, leading=12),
        "ref": ParagraphStyle("ref", parent=body, fontSize=9.5, leading=13, spaceAfter=4),
    }  # fmt: skip


def _build_pdf(path: Path, story: list) -> None:  # type: ignore[type-arg]
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, invariant=1, title=path.stem, author="CiteProof golden"
    )
    doc.build(story)


def make_vi_mixed_apa(path: Path) -> None:
    s = _styles()
    story: list = [  # type: ignore[type-arg]
        Paragraph("Kiểm tra trích dẫn trong luận văn công nghệ thông tin", s["title"]),
        Paragraph("Trần Quốc Dũng", s["body"]),
        Paragraph("Khoa Công nghệ Thông tin, Trường Đại học Mẫu, Hà Nội", s["caption"]),
        Paragraph(
            "Tóm tắt. Bài viết trình bày cách đối chiếu các trích dẫn trong luận văn tiếng "
            "Việt với danh mục tài liệu tham khảo bằng tiếng Việt và tiếng Anh.",
            s["body"],
        ),
        Paragraph("1. Giới thiệu", s["heading"]),
        Paragraph(
            "Các mô hình ngôn ngữ huấn luyện trước đã thay đổi cách tiếp cận xử lý văn bản "
            "tiếng Việt (Nguyễn Văn An, 2020; Devlin et al., 2019). Phần lớn công trình trong "
            "nước vẫn dựa vào các bài báo quốc tế để xây dựng phương pháp.",
            s["body"],
        ),
        Paragraph("2. Cơ sở lý thuyết", s["heading"]),
        Paragraph(
            "Kiến trúc Transformer là nền tảng của hầu hết các mô hình hiện nay "
            "(Vaswani et al., 2017). Việc đối chiếu từng nhận định với tài liệu gốc giúp "
            "người đọc kiểm tra được độ tin cậy của lập luận.",
            s["body"],
        ),
        PageBreak(),
        Paragraph("Tài liệu tham khảo", s["heading"]),
        *[Paragraph(entry, s["ref"]) for entry in MIXED_APA_REFS],
    ]
    _build_pdf(path, story)


def make_en_ieee(path: Path) -> None:
    s = _styles()
    table = Table(
        [["Model", "Accuracy"], ["Baseline LSTM", "81.2"], ["Fine-tuned encoder", "88.7"]],
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, "#555555"),
    ]))  # fmt: skip
    story: list = [  # type: ignore[type-arg]
        Paragraph("Sequence Models for Citation Checking", s["title"]),
        Paragraph("Alex Example and Sam Sample", s["body"]),
        Paragraph("Department of Computer Science, Example University", s["caption"]),
        Paragraph(
            "<b>Abstract</b>\N{EM DASH}We study how sequence models can be used to check that "
            "citations in a thesis match the cited sources. Experiments on a small sample set "
            "compare a recurrent baseline with a fine-tuned encoder.",
            s["body"],
        ),
        Paragraph("I. Introduction", s["heading"]),
        Paragraph(
            "Self-attention based models have become the standard for sequence transduction "
            "[1]. Pre-trained encoders and deep residual networks have been widely adopted "
            "[2]\N{EN DASH}[4].",
            s["body"],
        ),
        Paragraph("II. Method", s["heading"]),
        Paragraph(
            "A pre-trained encoder is fine-tuned on the target data [2]. Residual connections "
            "stabilise the training of deep models [3].",
            s["body"],
        ),
        PageBreak(),
        Paragraph("III. Results", s["heading"]),
        Paragraph(
            "Recurrent networks remain a strong baseline for short sequences [4]. "
            "Table I summarises the accuracy of both models.",
            s["body"],
        ),
        Paragraph("TABLE I. Accuracy of the baseline and the fine-tuned encoder.", s["caption"]),
        table,
        Spacer(1, 12),
        Paragraph("References", s["heading"]),
        *[Paragraph(f"[{n}] {entry}", s["ref"]) for n, entry in enumerate(IEEE_REFS, 1)],
    ]
    _build_pdf(path, story)


def _scan_image(seed: int) -> Image.Image:
    """Ảnh xám nhỏ, tất định, giả một trang quét (đường kẻ và khung, không có chữ)."""
    image = Image.new("L", (240, 340), 245)
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 229, 329), outline=90, width=2)
    for y in range(40, 320, 14):
        draw.line((24, y, 216 - (y * seed) % 60, y), fill=120, width=3)
    return image


def _draw_scan_page(canvas: Canvas, seed: int) -> None:
    canvas.drawInlineImage(_scan_image(seed), 90, 200, width=360, height=510)
    canvas.showPage()


def _text_page(canvas: Canvas, lines: list[str]) -> None:
    canvas.setFont("DejaVu", 11)
    y = 780
    for line in lines:
        canvas.drawString(60, y, line)
        y -= 18
    canvas.showPage()


def make_vi_scanned_pages(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setTitle(path.stem)
    _text_page(canvas, [
        "Báo cáo tiến độ thực hiện đề tài",
        "Trang này có lớp chữ nên trích xuất văn bản được bình thường.",
        "Trang kế tiếp là ảnh quét, không có lớp chữ.",
    ])  # fmt: skip
    _draw_scan_page(canvas, 3)
    _text_page(
        canvas,
        [
            "Trang cuối lại có lớp chữ để kiểm tra việc đánh số trang bắt đầu từ một.",
            "Kết thúc báo cáo.",
        ],
    )
    canvas.save()


def make_image_only(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setTitle(path.stem)
    for seed in (2, 5):
        _draw_scan_page(canvas, seed)
    canvas.save()


def make_encrypted(path: Path) -> None:
    canvas = Canvas(
        str(path),
        pagesize=A4,
        invariant=1,
        encrypt=StandardEncryption(PDF_PASSWORD, ownerPassword=PDF_PASSWORD + "-owner"),
    )
    canvas.setTitle(path.stem)
    _text_page(canvas, ["Tệp PDF được bảo vệ bằng mật khẩu."])
    canvas.save()


MAKERS: dict[str, Callable[[Path], None]] = {
    "vi_thesis_numeric.docx": make_vi_thesis_numeric,
    "en_paper_author_year.docx": make_en_paper_author_year,
    "vi_mixed_apa.pdf": make_vi_mixed_apa,
    "en_ieee.pdf": make_en_ieee,
    "vi_tcvn3_legacy.docx": make_vi_tcvn3_legacy,
    "vi_scanned_pages.pdf": make_vi_scanned_pages,
    "encrypted.pdf": make_encrypted,
    "image_only.pdf": make_image_only,
    "vi_text_input.json": make_vi_text_input,
    "multiline_refs.docx": make_multiline_refs,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    _register_fonts()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, maker in MAKERS.items():
        maker(args.out / name)
        print(f"{name}: {(args.out / name).stat().st_size} byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
