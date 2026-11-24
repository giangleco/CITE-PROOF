"""Parse tài liệu tham khảo bằng rule (QĐ-04): DOI/URL/ISBN/năm, tiêu đề, khối tác giả."""

import unicodedata

from citeproof.modules.references.rules import clean_title, parse_reference_rules

APA = (
    "Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K. (2019). BERT: Pre-training of deep "
    "bidirectional transformers for language understanding. In Proceedings of the 2019 "
    "Conference of the North American Chapter of the Association for Computational "
    "Linguistics (pp. 4171-4186). https://doi.org/10.18653/v1/N19-1423"
)
IEEE = (
    'J. Devlin, M.-W. Chang, K. Lee, and K. Toutanova, "BERT: Pre-training of deep '
    'bidirectional transformers for language understanding," in Proc. NAACL-HLT, 2019, '
    "pp. 4171-4186, doi: 10.18653/v1/N19-1423."
)
VI_THESIS = (
    "Trần Thị Bích (2021), Nhận dạng thực thể có tên trong văn bản tiếng Việt, "
    "Luận văn thạc sĩ, Trường Đại học Mẫu, Hà Nội."
)
BERT_TITLE = "BERT: Pre-training of deep bidirectional transformers for language understanding"


def test_apa_reference() -> None:
    ref = parse_reference_rules(APA)
    assert ref.title == BERT_TITLE
    assert ref.year == 2019
    assert ref.doi == "10.18653/v1/N19-1423"
    assert ref.url == "https://doi.org/10.18653/v1/N19-1423"
    assert ref.authors == ["Devlin, J.", "Chang, M.-W.", "Lee, K.", "Toutanova, K."]
    assert ref.isbn is None and ref.venue is None and ref.label is None


def test_ieee_reference() -> None:
    ref = parse_reference_rules(IEEE)
    assert ref.title == BERT_TITLE
    assert ref.year == 2019
    assert ref.doi == "10.18653/v1/N19-1423"
    assert ref.authors == ["J. Devlin", "M.-W. Chang", "K. Lee", "K. Toutanova"]


def test_vietnamese_thesis_reference() -> None:
    ref = parse_reference_rules(VI_THESIS)
    assert ref.title == "Nhận dạng thực thể có tên trong văn bản tiếng Việt"
    assert ref.year == 2021
    assert ref.authors == ["Trần Thị Bích"]
    assert ref.doi is None and ref.url is None


def test_vietnamese_book_and_quoted_titles() -> None:
    book = parse_reference_rules(
        "Lê Hoàng Cường (2019), Giáo trình xử lý ngôn ngữ tự nhiên, NXB Giáo dục Việt Nam, "
        "Hà Nội, ISBN 978-604-0-12345-6."
    )
    assert book.title == "Giáo trình xử lý ngôn ngữ tự nhiên"
    assert book.isbn == "978-604-0-12345-6"
    curly = parse_reference_rules(
        "Nguyễn Văn An, Trần Thị Bích (2020), “Phân loại văn bản tiếng Việt”, Tạp chí Mẫu, "
        "12(3), tr. 45-58."
    )
    assert curly.title == "Phân loại văn bản tiếng Việt"
    assert curly.authors == ["Nguyễn Văn An", "Trần Thị Bích"]
    assert curly.year == 2020
    vaswani = parse_reference_rules(
        'Vaswani A., Shazeer N., Parmar N., et al. (2017), "Attention is all you need", '
        "arXiv preprint arXiv:1706.03762, doi:10.48550/arXiv.1706.03762."
    )
    assert vaswani.title == "Attention is all you need"
    assert vaswani.authors == ["Vaswani A.", "Shazeer N.", "Parmar N."]
    assert vaswani.doi == "10.48550/arXiv.1706.03762"
    va = parse_reference_rules("Nguyễn Văn An và Lê Văn Bình (2018). Một tiêu đề. Tạp chí.")
    assert va.authors == ["Nguyễn Văn An", "Lê Văn Bình"]
    assert va.title == "Một tiêu đề"


def test_year_skips_identifiers_and_access_dates() -> None:
    electra = parse_reference_rules('K. Clark et al., "ELECTRA," arXiv:2003.10555, 2020.')
    assert electra.year == 2020
    web = parse_reference_rules(
        "Bộ Giáo dục và Đào tạo, Thông tư số 08, https://moet.gov.vn/van-ban, truy cập 12/3/2024."
    )
    assert web.year is None
    assert web.url == "https://moet.gov.vn/van-ban"
    doi_only = parse_reference_rules("Smith, J. Title. https://doi.org/10.1109/CVPR.2016.90")
    assert doi_only.year is None
    pages = parse_reference_rules("Hochreiter, S. Long memory. Neural, pp. 1901-1950, 1997.")
    assert pages.year == 1997


def test_title_ends_at_question_mark() -> None:
    ref = parse_reference_rules("Smith, J. (2020). Is attention all you need? Journal X, 3(1).")
    assert ref.title == "Is attention all you need?"


def test_bare_year_after_title_gives_no_authors() -> None:
    ref = parse_reference_rules(
        "K. He, X. Zhang, Deep residual learning, in Proc. IEEE CVPR, 2016, pp. 770-778."
    )
    assert ref.year == 2016
    assert ref.authors == []
    assert ref.title is None


def test_author_block_must_look_like_names() -> None:
    title_first = parse_reference_rules(
        "Nguyễn Văn An, Phân loại văn bản tiếng Việt, Tạp chí Mẫu (2020)."
    )
    assert title_first.authors == []
    particle = parse_reference_rules("van der Maaten, L., & Hinton, G. (2008). Visualizing data.")
    assert particle.authors == ["van der Maaten, L.", "Hinton, G."]


def test_bare_year_after_name_list() -> None:
    ref = parse_reference_rules("Nguyễn Văn An, Lê Văn Bình, 2020. Tiêu đề bài báo. Tạp chí.")
    assert ref.authors == ["Nguyễn Văn An", "Lê Văn Bình"]
    assert ref.year == 2020
    assert ref.title == "Tiêu đề bài báo"


def test_unstructured_reference() -> None:
    ref = parse_reference_rules("NGUYỄN VĂN AN. PHÂN LOẠI VĂN BẢN TIẾNG VIỆT. TẠP CHÍ MẪU 2020")
    assert ref.title is None
    assert ref.authors == []
    assert ref.year == 2020
    empty = parse_reference_rules("Ghi chú không có cấu trúc")
    assert (empty.title, empty.year, empty.doi, empty.url, empty.isbn) == (None,) * 5


def test_fields_are_nfc() -> None:
    ref = parse_reference_rules(unicodedata.normalize("NFD", VI_THESIS))
    assert ref.title == "Nhận dạng thực thể có tên trong văn bản tiếng Việt"
    assert ref.authors == ["Trần Thị Bích"]


def test_clean_title_cuts_publication_details() -> None:
    assert clean_title(
        "Nhận dạng thực thể có tên trong văn bản tiếng Việt, Luận văn thạc sĩ, Trường Đại học Mẫu"
    ) == ("Nhận dạng thực thể có tên trong văn bản tiếng Việt")
    assert clean_title("Giáo trình xử lý ngôn ngữ tự nhiên, NXB Giáo dục Việt Nam") == (
        "Giáo trình xử lý ngôn ngữ tự nhiên"
    )
    assert clean_title(" “Attention is all you need,” ") == "Attention is all you need"
    assert clean_title("Lịch sử, văn hóa và xã hội") == "Lịch sử, văn hóa và xã hội"
    assert clean_title(" , . ") is None
