"""Parse tài liệu tham khảo bằng rule, tất định (spec §7.2, QĐ-04).

- DOI (`10.<4-9 chữ số>/…`, giữ như in, bỏ dấu câu cuối), URL `http(s)://` đầu tiên, ISBN
  (10 hoặc 13 chữ số sau chữ "ISBN").
- Năm: năm đầu tiên trong 1800-2099 không dính vào định danh (DOI, arXiv, URL, ngày truy
  cập `12/3/2024`, khoảng trang `1901-1950`).
- Tiêu đề: đoạn trong ngoặc kép “…”/"…"; không có thì đoạn sau năm tới dấu `.`/`?`/`!` kế
  tiếp. `clean_title` cắt phần thông tin xuất bản dính sau dấu phẩy (", Luận văn thạc sĩ…",
  ", NXB…"), lỗi hay gặp của cả rule và GROBID với danh mục tiếng Việt.
- Tác giả: đoạn trước năm (hoặc trước tiêu đề trong ngoặc kép), tách theo `,` `;` ` và `
  ` and ` `&`; phần chỉ gồm chữ viết tắt nối vào phần trước ("Devlin", "J." → "Devlin, J.").
  Khối chỉ được nhận khi mọi phần trông như tên người (ít từ, từ viết hoa, chữ viết tắt hoặc
  tiểu từ "van"/"de"), để tiêu đề hay tên tạp chí đứng trước năm không thành tác giả; khi đó
  S2 dùng tác giả của GROBID.

Mọi trường trả về ở dạng NFC.
"""

import re

from citeproof.core.domain import ParsedReference
from citeproof.modules.references.normalize import is_initial, nfc

_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+")
_DOI_TRAILING = ".,;)]"
_URL = re.compile(r"https?://[^\s\"<>]+", re.IGNORECASE)
_URL_TRAILING = ".,;)]"
_ISBN = re.compile(r"\bISBN(?:-1[03])?:?\s*([0-9][0-9 \-]{8,}[0-9Xx])", re.IGNORECASE)
_YEAR = re.compile(r"(?<![\w./:\-])(1[89]\d{2}|20\d{2})[a-z]?(?![\w/]|[.\-]\d)")
_QUOTED = re.compile(r"“([^”]{3,})”|\"([^\"]{3,})\"")
_YEAR_TO_TITLE = r"[a-z]?\)?[\s.,:;]*"
_YEAR_TO_QUOTE = re.compile(_YEAR_TO_TITLE)
_TITLE_AFTER_YEAR = re.compile(
    _YEAR_TO_TITLE + r"(?P<title>.+?)(?:[.](?=\s|$)|(?<=[?!])(?=\s|$)|$)"
)
_PUBLICATION_DETAILS = re.compile(
    r",\s*(?=(?:luận văn|luận án|khóa luận|khoá luận|đồ án|báo cáo|nxb|nhà xuất bản|"
    r"trường đại học|đại học|tạp chí|kỷ yếu|hội nghị|in proceedings|proceedings|"
    r"master'?s thesis|ph\.?\s?d\.? thesis|doctoral thesis|dissertation)\b)",
    re.IGNORECASE,
)
_TITLE_EDGES = " \t\"'\u201c\u201d\u2018\u2019\u00ab\u00bb.,;:"
_NOT_TITLE_START = re.compile(
    r"(?:pp?\.|tr\.|vol\.|no\.|doi|https?:|isbn|in\s|retrieved|truy cập|accessed)",
    re.IGNORECASE,
)
_MAX_TITLE_CHARS = 400
_ET_AL = re.compile(
    r",?\s*(?:et\s+al\.?|và\s+cộng\s+sự|và\s+cs\.?|và\s+nnk\.?|and\s+others)", re.IGNORECASE
)
# Dấu phẩy/chấm phẩy (kèm "&", "và", "and" ngay sau), hoặc "&"/"và"/"and" đứng giữa hai từ.
_AUTHOR_SPLIT = re.compile(r"\s*[,;]\s*(?:(?:&|và|and)\s+)?|\s+(?:&|và|and)\s+", re.IGNORECASE)
_AUTHOR_EDGES = " ,;:&("
_MAX_AUTHOR_SEGMENT = 500
_MAX_NAME_TOKENS = 6
# Tiểu từ viết thường trong họ ("van der Maaten", "de Souza").
_NAME_PARTICLES = frozenset(
    {"van", "von", "der", "den", "de", "del", "da", "di", "du", "dos", "la"}
)


def clean_title(title: str | None) -> str | None:
    """Tiêu đề bỏ ngoặc kép, dấu câu hai đầu và phần xuất bản dính sau dấu phẩy."""
    if title is None:
        return None
    text = nfc(title).strip(_TITLE_EDGES)
    match = _PUBLICATION_DETAILS.search(text)
    if match is not None and match.start() > 0:
        text = text[: match.start()]
    text = text.strip(_TITLE_EDGES)
    return text or None


def _doi(raw: str) -> str | None:
    match = _DOI.search(raw)
    return match.group(0).rstrip(_DOI_TRAILING) if match else None


def _url(raw: str) -> str | None:
    match = _URL.search(raw)
    return match.group(0).rstrip(_URL_TRAILING) if match else None


def _isbn(raw: str) -> str | None:
    for match in _ISBN.finditer(raw):
        value = match.group(1).strip(" -")
        digits = re.sub(r"[^0-9Xx]", "", value)
        if len(digits) in (10, 13):
            return value
    return None


def _quoted_title(raw: str, year: re.Match[str] | None) -> re.Match[str] | None:
    """Đoạn trong ngoặc kép ở vị trí tiêu đề: trước năm (IEEE) hoặc ngay sau năm ("(2017), "…"").

    Ngoặc kép nằm giữa một tiêu đề APA ("The "best" approach") không phải tiêu đề.
    """
    quoted = _QUOTED.search(raw)
    if quoted is None or year is None or quoted.start() < year.start():
        return quoted
    gap = raw[year.end() : quoted.start()]
    return quoted if _YEAR_TO_QUOTE.fullmatch(gap) else None


def _title_after_year(raw: str, year: re.Match[str]) -> str | None:
    match = _TITLE_AFTER_YEAR.match(raw, year.end())
    if match is None:
        return None
    title = match.group("title").strip()
    if (
        len(title.split()) < 2
        or len(title) > _MAX_TITLE_CHARS
        or _NOT_TITLE_START.match(title)
        or title[0].isdigit()
    ):
        return None
    return title


def _name_like(parts: list[str]) -> bool:
    """Mọi phần có dáng tên người: ít từ, mỗi từ viết hoa chữ đầu hoặc là chữ viết tắt."""
    for part in parts:
        tokens = part.split()
        if not tokens or len(tokens) > _MAX_NAME_TOKENS:
            return False
        if not all(
            token[0].isupper() or is_initial(token) or token.lower() in _NAME_PARTICLES
            for token in tokens
        ):
            return False
    return True


def _split_authors(segment: str) -> list[str]:
    text = _ET_AL.sub("", segment)
    authors: list[str] = []
    for part in (piece.strip(_AUTHOR_EDGES) for piece in _AUTHOR_SPLIT.split(text)):
        if not part:
            continue
        if authors and all(is_initial(token) for token in part.split()):
            authors[-1] = f"{authors[-1]}, {part}"  # APA: "Devlin", "J." → "Devlin, J."
        else:
            authors.append(part)
    return authors


def _authors(raw: str, quoted: re.Match[str] | None, year: re.Match[str] | None) -> list[str]:
    if quoted is not None and (year is None or quoted.start() < year.start()):
        end = quoted.start()
    elif year is not None:
        prefix = raw[: year.start()].rstrip()
        end = len(prefix) - 1 if prefix.endswith("(") else year.start()
    else:
        return []
    segment = raw[:end].strip()
    if not segment or len(segment) > _MAX_AUTHOR_SEGMENT or any(ch.isdigit() for ch in segment):
        return []
    authors = _split_authors(segment)
    return authors if _name_like(authors) else []


def parse_reference_rules(raw: str) -> ParsedReference:
    """Trường tách được bằng rule từ chuỗi tài liệu tham khảo (không có nhãn)."""
    text = nfc(raw).strip()
    year = _YEAR.search(text)
    quoted = _quoted_title(text, year)
    if quoted is not None:
        title = clean_title(quoted.group(1) or quoted.group(2))
    elif year is not None:
        title = clean_title(_title_after_year(text, year))
    else:
        title = None
    return ParsedReference(
        title=title,
        authors=_authors(text, quoted, year),
        year=int(year.group(1)) if year else None,
        doi=_doi(text),
        isbn=_isbn(text),
        url=_url(text),
    )
