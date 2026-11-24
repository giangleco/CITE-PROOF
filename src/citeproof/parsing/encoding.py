"""Phát hiện văn bản tiếng Việt gõ bằng bảng mã cũ (QĐ-05, spec §7.1).

Chỉ cảnh báo (`LEGACY_ENCODING`); chuyển đổi sang Unicode nằm ngoài phạm vi.

- TCVN3 (ABC, phông .VnTime) đọc như Latin-1: ă â ê ô ơ ư đ và nhóm a có dấu rơi vào
  vùng byte A1-BE (ví dụ © ª « ¬ ® µ ¶). Không bảng mã Unicode tiếng Việt hay VNI nào
  sinh ra các ký tự này, nên hai phép kiểm không lẫn nhau. Điều kiện: ≥ 20 ký tự đặc trưng,
  chiếm ≥ 30% số ký tự ngoài ASCII (chữ cái và ký tự đặc trưng), và thêm ≥ 5% số chữ
  cái: văn bản TCVN3 thật có mật độ cỡ 10-15%, còn tài liệu tiếng Anh dùng nhiều ·, µ,
  ¬ trong công thức chỉ có mật độ rất thấp.
- VNI đọc như cp1252: nguyên âm theo sau là ký tự dấu (`aø`, `eä`, `oä`). Điều kiện:
  ≥ 5 cặp trên mỗi 1 000 ký tự; văn bản ngắn hơn 1 000 ký tự vẫn cần đủ 5 cặp.
"""

import re
from typing import Literal

LegacyEncoding = Literal["TCVN3", "VNI"]

TCVN3_SIGNATURE = frozenset(
    chr(code)
    for block in (range(0xA1, 0xAF), range(0xB5, 0xBA), range(0xBB, 0xBF))
    for code in block
)
TCVN3_MIN_CHARS = 20
TCVN3_MIN_NON_ASCII_SHARE = 0.30
TCVN3_MIN_LETTER_SHARE = 0.05
VNI_PAIR = re.compile(r"[aeiouyAEIOUY][øùûõïåäëöü]")
VNI_MIN_PER_1000 = 5


def _is_tcvn3(text: str) -> bool:
    signature = sum(char in TCVN3_SIGNATURE for char in text)
    if signature < TCVN3_MIN_CHARS:
        return False
    non_ascii = sum(
        ord(char) > 0x7F and (char.isalpha() or char in TCVN3_SIGNATURE) for char in text
    )
    letters = sum(char.isalpha() or char in TCVN3_SIGNATURE for char in text)
    return (
        signature >= TCVN3_MIN_NON_ASCII_SHARE * non_ascii
        and signature >= TCVN3_MIN_LETTER_SHARE * letters
    )


def _is_vni(text: str) -> bool:
    pairs = len(VNI_PAIR.findall(text))
    return pairs * 1000 >= VNI_MIN_PER_1000 * max(len(text), 1000)


def detect_legacy_encoding(text: str) -> LegacyEncoding | None:
    """`"TCVN3"`, `"VNI"` hoặc `None` khi văn bản không có dấu hiệu bảng mã cũ."""
    if _is_tcvn3(text):
        return "TCVN3"
    if _is_vni(text):
        return "VNI"
    return None
