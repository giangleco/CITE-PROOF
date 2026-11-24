"""Bộ golden parsing (T-208): mô hình kết quả mong đợi và bộ nạp `load_cases()`.

Fixture nằm ở `tests/fixtures/golden/`. Quy ước đặt tên cạnh tệp đầu vào `<name>.<ext>`:

- `<name>.expected.json`: kết quả mong đợi (`GoldenExpected`), viết tay và kiểm tra bằng mắt;
- `<name>.fulltext.tei.xml`: TEI GROBID đã ghi cho PDF (`processFulltextDocument`);
- `<name>.citations.tei.xml`: TEI GROBID đã ghi cho danh sách tài liệu tham khảo của
  DOCX/JSON (`processCitationList`).

`GoldenCitation.links` là nhãn tài liệu tham khảo; tài liệu không in nhãn (kiểu tác giả-năm)
dùng thứ tự 1-based trong danh sách tham khảo (dạng chuỗi), `None` là chưa liên kết được.
`GoldenCitation.claim` là đoạn câu trước marker theo QĐ-16 (bỏ dấu câu và khoảng trắng hai đầu).
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from citeproof.core.enums import Language, RefType

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "golden"
INPUT_SUFFIXES = (".docx", ".pdf", ".json")
_EXPECTED_SUFFIX = ".expected.json"


class GoldenRef(BaseModel):
    label: str | None
    title: str | None
    year: int | None
    doi: str | None
    first_author_family: str | None
    ref_type: RefType


class GoldenCitation(BaseModel):
    marker: str
    links: list[str | None]
    claim: str


class GoldenExpected(BaseModel):
    language: Language
    warnings: list[str]
    page_count: int | None
    references: list[GoldenRef]
    citations: list[GoldenCitation]


@dataclass(frozen=True)
class GoldenCase:
    name: str
    input_path: Path
    expected: GoldenExpected | None
    fulltext_tei: Path | None
    citations_tei: Path | None


def _existing(path: Path) -> Path | None:
    return path if path.exists() else None


def load_cases() -> list[GoldenCase]:
    """Nạp mọi tệp đầu vào golden, sắp theo tên; tệp lỗi (mã hóa, ảnh) không có `expected`."""
    cases: list[GoldenCase] = []
    for path in sorted(GOLDEN_DIR.iterdir()):
        if path.suffix not in INPUT_SUFFIXES or path.name.endswith(_EXPECTED_SUFFIX):
            continue
        expected_path = GOLDEN_DIR / f"{path.stem}{_EXPECTED_SUFFIX}"
        expected = (
            GoldenExpected.model_validate_json(expected_path.read_text(encoding="utf-8"))
            if expected_path.exists()
            else None
        )
        cases.append(
            GoldenCase(
                name=path.stem,
                input_path=path,
                expected=expected,
                fulltext_tei=_existing(GOLDEN_DIR / f"{path.stem}.fulltext.tei.xml"),
                citations_tei=_existing(GOLDEN_DIR / f"{path.stem}.citations.tei.xml"),
            )
        )
    return cases
