"""Phân loại tài liệu bằng rule (spec §7.2)."""

import pytest

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import RefType
from citeproof.modules.references.classify import classify_ref_type
from citeproof.modules.references.rules import parse_reference_rules


@pytest.mark.parametrize(
    ("raw", "ref_type"),
    [
        (
            "Vaswani, A. (2017). Attention is all you need. arXiv preprint arXiv:1706.03762",
            RefType.PREPRINT,
        ),
        (
            "Devlin, J. (2019). BERT. In Proceedings of NAACL-HLT (pp. 4171-4186).",
            RefType.CONFERENCE,
        ),
        ('K. He, "Deep residual learning," in Proc. IEEE CVPR, 2016.', RefType.CONFERENCE),
        ("Nguyễn Văn An (2020), Bài báo, Kỷ yếu Hội nghị Quốc gia, tr. 1-5.", RefType.CONFERENCE),
        (
            "Trần Thị Bích (2021), Nhận dạng thực thể, Luận văn thạc sĩ, Trường Đại học Mẫu.",
            RefType.THESIS,
        ),
        ("Smith, J. (2010). Parsing. PhD thesis, Stanford University.", RefType.THESIS),
        (
            "Lê Hoàng Cường (2019), Giáo trình, NXB Giáo dục, ISBN 978-604-0-12345-6.",
            RefType.BOOK,
        ),
        ("Jurafsky, D. (2009). Speech and language processing. Prentice Hall Press.", RefType.BOOK),
        (
            "Bộ Giáo dục và Đào tạo, Thông tư số 08, https://example.org, truy cập 12/3/2024.",
            RefType.WEB,
        ),
        ("OpenAI (2023). GPT-4. https://openai.com/research, accessed 1 May 2024.", RefType.WEB),
        (
            'S. Hochreiter, "Long short-term memory," Neural Comput., vol. 9, no. 8, '
            "pp. 1735-1780, 1997.",
            RefType.ARTICLE,
        ),
        ("Nguyễn Văn An (2020), Bài báo, Tạp chí Mẫu, 12(3), 45-58.", RefType.ARTICLE),
        ("Nguyễn Văn An (2020). Ghi chép riêng.", RefType.UNKNOWN),
        # Chữ số trong DOI/URL không phải số trang.
        ("Smith, J. (2020). Title. https://doi.org/10.18653/v1/N19-1423", RefType.UNKNOWN),
        # Có DOI thì không phải trang web dù có "truy cập".
        ("Smith, J. (2020). Title. https://doi.org/10.1234/abcd, truy cập 2024.", RefType.UNKNOWN),
    ],
)
def test_classify(raw: str, ref_type: RefType) -> None:
    assert classify_ref_type(raw, parse_reference_rules(raw)) is ref_type


def test_classify_uses_parsed_url() -> None:
    raw = "Trang chủ dự án, truy cập ngày 2/1/2024."
    assert classify_ref_type(raw, ParsedReference()) is RefType.UNKNOWN
    assert classify_ref_type(raw, ParsedReference(url="https://example.org")) is RefType.WEB
