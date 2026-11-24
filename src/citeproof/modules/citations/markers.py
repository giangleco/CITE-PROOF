"""Phát hiện marker trích dẫn trong một câu bằng luật (spec §7.4, QĐ-23).

**Marker số** `[…]`: nhóm phân tách bằng `;`, phần tử bằng `,`; mỗi phần tử là một số hoặc một
khoảng (`2-4` → 2, 3, 4, gạch nối hoặc gạch ngang dài; tối đa `MAX_NUMBERS` số sau khi mở
rộng), có thể kèm vị trí trang của luận văn Việt: `[1, tr. 25]`, `[1, trang 25]`,
`[1, pp. 25-30]`, `[1: 25]`, `[1, tr. 25; 3, tr. 7]` (mở rộng spec §7.4 theo phán quyết
Task 10). Số trang không phải số tài liệu (không được liên kết); `marker.text` giữ cả ngoặc.
Khoảng IEEE viết bằng hai ngoặc (`[2]-[4]`, kể cả gạch ngang dài; GROBID ghi `[2]- [4]`) là
một marker. Không phải marker: trang không có số tài liệu (`[tr. 25]`), số 0, khoảng ngược
(`[5-3]`), phần rỗng (`[1,]`), quá `MAX_NUMBERS` số, và ngoặc mà mọi số tài liệu đều là năm
1800-2099 (`[2019]`). Thứ tự và số lặp giữ nguyên như trong bài.

**Marker tác giả-năm:**
- Trong ngoặc tròn: các phần `Tên[ et al.| và cs.| & Tên], YYYY[a-z][, YYYY…][, tr. N]` phân
  tách bằng `;`; mọi phần phải hợp lệ, ngược lại cả ngoặc không phải marker. Có thể mở đầu
  bằng "xem", "see", "e.g.," hay "cf.".
- Tường thuật `Tên (YYYY)`: chuỗi tên ngay trước ngoặc chỉ chứa năm. Trong chuỗi tên viết hoa
  đứng trước, tên tác giả bắt đầu ở họ người Việt đầu tiên ("Theo Nguyễn và cs. (2020)" →
  "Nguyễn"); không có họ Việt thì là từ cuối ("In Vaswani et al. (2017)" → "Vaswani"), và từ
  đó không được có chữ chỉ tiếng Việt mới có ("Năm (2019)" không phải trích dẫn) hay viết hoa
  toàn bộ (tên viết tắt như "BERT").

Năm luôn trong 1800-2099. `AuthorYear.name` là tác giả đầu như viết trong bài (khoảng trắng
gộp lại): hai tác giả nối bằng `&`/`and`/`và` thì lấy người đầu, trừ khi phần trước từ nối có
chữ thường (tên tổ chức như "Bộ Giáo dục và Đào tạo" giữ nguyên).

Vị trí `start`/`end` tính trong câu (`sentence[start:end] == marker.text`); marker trả về theo
thứ tự xuất hiện, không chồng lấn nhau.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from citeproof.core.enums import LinkMethod
from citeproof.modules.references.normalize import VIETNAMESE_FAMILY_NAMES, fold, is_initial

__all__ = ["MAX_NUMBERS", "AuthorYear", "Marker", "find_markers", "numeric_marker_numbers"]

MAX_NUMBERS = 50
MIN_YEAR = 1800
MAX_YEAR = 2099
MAX_NAME_TOKENS = 10

_YEAR = r"(?:18\d\d|19\d\d|20\d\d)"
_DASH = "\\-\u2013"  # gạch nối (đã thoát, dùng trong lớp ký tự) và gạch ngang dài
_PAGES = rf"(?:\s*,\s*(?:pp?|tr|trang)\.?\s*\d+(?:\s*[{_DASH}]\s*\d+)?)?"
_NUMERIC = re.compile(
    rf"\[\s*(?P<a>\d{{1,4}})\s*\]\s*[{_DASH}]\s*\[\s*(?P<b>\d{{1,4}})\s*\]"  # khoảng IEEE
    r"|\[(?P<body>[^\[\]]{1,200})\]"
)
_NUMERIC_PART = re.compile(rf"(\d{{1,4}})(?:\s*[{_DASH}]\s*(\d{{1,4}}))?")
_PAGE_RANGE = rf"\d{{1,5}}(?:\s*[{_DASH}]\s*\d{{1,5}})?"
# Vị trí trang sau số tài liệu: "tr. 25", "trang 25", "tr.25-30", "p. 25", "pp. 25-30".
_LOCATOR = re.compile(rf"(?:trang|tr|pp|p)\.?\s*{_PAGE_RANGE}", re.IGNORECASE)
# Số tài liệu kèm trang sau dấu hai chấm: "1: 25", "1:25-30".
_COLON_LOCATOR = re.compile(rf"(\d{{1,4}})\s*:\s*{_PAGE_RANGE}")
_PARENTHETICAL = re.compile(r"\(([^()]{1,400})\)")
_CITE_PART = re.compile(
    rf"(?P<names>[^,\d]+?)\s*,\s*(?P<years>{_YEAR}[a-z]?(?:\s*,\s*{_YEAR}[a-z]?)*){_PAGES}"
)
_NARRATIVE = re.compile(rf"\((?P<years>{_YEAR}[a-z]?(?:\s*[,;]\s*{_YEAR}[a-z]?)*){_PAGES}\)")
_YEAR_ITEM = re.compile(rf"({_YEAR})([a-z]?)")
_PREFIX = re.compile(r"(?:see also|see|e\.g\.,?|cf\.|xem thêm|xem)\s+", re.IGNORECASE)
_APOSTROPHES = "'\u2019"
_NAME_TOKEN = re.compile(rf"[^\W\d_](?:[^\W\d_]|[{_APOSTROPHES}.\-])*")
_TOKEN = re.compile(r"\S+")
_POSSESSIVE = re.compile(rf"[{_APOSTROPHES}]s$")

_CONNECTORS = frozenset({"&", "and", "va"})
# Đuôi "et al." (đã gấp dấu), dài trước.
_ET_AL = (
    ("va", "cac", "cong", "su"),
    ("va", "cong", "su"),
    ("et", "al."),
    ("et", "al"),
    ("va", "cs."),
    ("va", "cs"),
    ("va", "nnk."),
    ("va", "nnk"),
)
_ET_AL_WORDS = frozenset(word for tail in _ET_AL for word in tail)
_PARTICLES = frozenset(
    {"van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "dos", "das", "ter"}
)
# Chữ chỉ tiếng Việt mới có (ă đ ĩ ũ ơ ư và khối Latin Extended Additional).
_VI_SPECIFIC = frozenset("ăđĩũơưĂĐĨŨƠƯ") | frozenset(chr(c) for c in range(0x1EA0, 0x1EFA))


@dataclass(frozen=True)
class AuthorYear:
    name: str
    year: int
    suffix: str | None


@dataclass(frozen=True)
class Marker:
    text: str
    start: int
    end: int
    kind: LinkMethod
    numbers: tuple[int, ...]
    cites: tuple[AuthorYear, ...]


# --- Marker số ------------------------------------------------------------------------------


def _is_year(number: int) -> bool:
    return MIN_YEAR <= number <= MAX_YEAR


def _expand(first: int, last: int) -> list[int] | None:
    if first < 1 or last < first or last - first + 1 > MAX_NUMBERS:
        return None
    return list(range(first, last + 1))


def _checked(numbers: list[int]) -> tuple[int, ...] | None:
    if not numbers or len(numbers) > MAX_NUMBERS or all(_is_year(n) for n in numbers):
        return None  # "[2019]": năm trong ngoặc, không phải trích dẫn
    return tuple(numbers)


def numeric_marker_numbers(body: str) -> tuple[int, ...] | None:
    """Số tài liệu trong ngoặc vuông (`body` là phần giữa `[` và `]`), theo thứ tự, khoảng đã
    mở rộng, bỏ số trang; None nếu không phải marker.

    Nhóm phân tách bằng `;`, phần tử trong nhóm bằng `,`. Phần tử là số, khoảng, số kèm trang
    sau dấu hai chấm (`1: 25`), hoặc vị trí trang (`tr. 25`) đứng ngay sau một số tài liệu.
    Sau vị trí trang, số còn lại trong nhóm là trang (`[1, tr. 25, 30]` → 1); vị trí trang thứ
    hai trong cùng nhóm (`[1, tr. 25, 3, tr. 7]`) là mơ hồ nên cả ngoặc không phải marker.
    """
    numbers: list[int] = []
    for group in body.split(";"):
        after_number = False  # phần tử trước là một số tài liệu chưa có trang
        paged = False  # nhóm đã có vị trí trang: số sau đó là trang
        for item in (part.strip() for part in group.split(",")):
            if _LOCATOR.fullmatch(item):
                if not after_number or paged:
                    return None  # "[tr. 25]": trang không có số tài liệu
                after_number, paged = False, True
                continue
            colon = _COLON_LOCATOR.fullmatch(item)
            match = None if colon is not None else _NUMERIC_PART.fullmatch(item)
            if paged:
                if match is None:
                    return None
                continue  # trang tiếp theo: "tr. 25, 30"
            if colon is not None:
                first = last = int(colon.group(1))
                paged = True
            elif match is not None:
                first = int(match.group(1))
                last = int(match.group(2)) if match.group(2) else first
            else:
                return None
            expanded = _expand(first, last)
            if expanded is None:
                return None
            numbers.extend(expanded)
            after_number = match is not None and match.group(2) is None
    return _checked(numbers)


def _numeric_markers(sentence: str) -> Iterator[Marker]:
    for match in _NUMERIC.finditer(sentence):
        if match.group("body") is not None:
            numbers = numeric_marker_numbers(match.group("body"))
        else:
            expanded = _expand(int(match.group("a")), int(match.group("b")))
            numbers = None if expanded is None else _checked(expanded)
        if numbers is not None:
            yield Marker(
                text=match.group(),
                start=match.start(),
                end=match.end(),
                kind=LinkMethod.NUMERIC,
                numbers=numbers,
                cites=(),
            )


# --- Tên tác giả ----------------------------------------------------------------------------


def _strip_et_al(tokens: list[str]) -> list[str]:
    folded = [fold(token) for token in tokens]
    for tail in _ET_AL:
        if len(folded) > len(tail) and tuple(folded[-len(tail) :]) == tail:
            return tokens[: -len(tail)]
    return tokens


def _capitalised(token: str) -> bool:
    return token[0].isupper()


def _person_like(tokens: Sequence[str]) -> bool:
    """Mọi từ viết hoa đầu (hoặc là tiểu từ họ như "van", "de"): dáng tên người."""
    return all(_capitalised(token) or token in _PARTICLES for token in tokens)


def _first_author(tokens: list[str]) -> list[str] | None:
    """Các từ của tác giả đầu trong chuỗi tên (đã bỏ "et al."); None nếu không phải tên."""
    tokens = _strip_et_al(tokens)
    if not tokens or len(tokens) > MAX_NAME_TOKENS:
        return None
    if not all(token == "&" or _NAME_TOKEN.fullmatch(token) for token in tokens):
        return None
    first = tokens[0]
    if first == "&" or not (_capitalised(first) or first in _PARTICLES):
        return None
    for position, token in enumerate(tokens):
        if fold(token) in _CONNECTORS:
            before = tokens[:position]
            if not before or not tokens[position + 1 :]:
                return None
            if _person_like(before):
                return before
            break  # tên tổ chức có "và"/"and": giữ nguyên
    return tokens


def _cite_years(years: str, name: str) -> list[AuthorYear]:
    return [
        AuthorYear(name=name, year=int(year), suffix=suffix or None)
        for year, suffix in _YEAR_ITEM.findall(years)
    ]


# --- Marker tác giả-năm trong ngoặc tròn ----------------------------------------------------


def _parenthetical_part(part: str) -> list[AuthorYear] | None:
    text = part.strip()
    prefix = _PREFIX.match(text)
    if prefix is not None:
        text = text[prefix.end() :]
    match = _CITE_PART.fullmatch(text)
    if match is None:
        return None
    author = _first_author(match.group("names").split())
    if author is None:
        return None
    return _cite_years(match.group("years"), " ".join(author))


def _parenthetical_markers(sentence: str) -> Iterator[Marker]:
    for match in _PARENTHETICAL.finditer(sentence):
        cites: list[AuthorYear] = []
        for part in match.group(1).split(";"):
            parsed = _parenthetical_part(part)
            if parsed is None:
                break
            cites.extend(parsed)
        else:
            yield Marker(
                text=match.group(),
                start=match.start(),
                end=match.end(),
                kind=LinkMethod.AUTHOR_YEAR,
                numbers=(),
                cites=tuple(cites),
            )


# --- Marker tường thuật "Tên (YYYY)" --------------------------------------------------------


def _run_token(token: str) -> bool:
    """Từ có thể nằm trong chuỗi tên trước "(YYYY)": tên viết hoa đầu, từ nối, "et al."."""
    folded = fold(token)
    if folded in _CONNECTORS or folded in _ET_AL_WORDS:
        return True
    return _capitalised(token) and _NAME_TOKEN.fullmatch(token) is not None


def _name_run(sentence: str, end: int) -> list[re.Match[str]]:
    """Các từ liền trước vị trí `end` (chỉ cách nhau bằng khoảng trắng) có dáng chuỗi tên."""
    prefix = sentence[:end]
    if prefix and not prefix[-1].isspace() and not prefix[-1].isalpha() and prefix[-1] != ".":
        return []
    tokens = list(_TOKEN.finditer(prefix))
    run: list[re.Match[str]] = []
    for token in reversed(tokens):
        if not _run_token(token.group()) or len(run) >= MAX_NAME_TOKENS:
            break
        run.append(token)
    run.reverse()
    while run and fold(run[0].group()) in _CONNECTORS | _ET_AL_WORDS:
        run.pop(0)  # chuỗi tên không mở đầu bằng từ nối
    return run


def _narrative_author(run: list[re.Match[str]]) -> list[re.Match[str]] | None:
    """Các từ của tác giả đầu trong chuỗi tên tường thuật (đã bỏ từ đứng trước tên)."""
    words = [token.group() for token in run]
    author = _first_author(words)
    if author is None:
        return None
    tokens = run[: len(author)]
    families = [
        index
        for index, token in enumerate(tokens)
        if fold(token.group().strip(",;.")) in VIETNAMESE_FAMILY_NAMES
    ]
    if families:
        return tokens[families[0] :]
    surname = tokens[-1].group()
    if (
        len(surname) < 2
        or surname.isupper()
        or is_initial(surname)
        or any(char in _VI_SPECIFIC for char in surname)
    ):
        return None
    return tokens[-1:]


def _narrative_markers(sentence: str) -> Iterator[Marker]:
    for match in _NARRATIVE.finditer(sentence):
        run = _name_run(sentence, match.start())
        author = _narrative_author(run) if run else None
        if author is None:
            continue
        name = _POSSESSIVE.sub("", " ".join(token.group() for token in author))
        start = author[0].start()
        yield Marker(
            text=sentence[start : match.end()],
            start=start,
            end=match.end(),
            kind=LinkMethod.AUTHOR_YEAR,
            numbers=(),
            cites=tuple(_cite_years(match.group("years"), name)),
        )


def find_markers(sentence: str) -> list[Marker]:
    """Mọi marker trong câu, theo thứ tự xuất hiện; marker chồng lên marker trước bị bỏ."""
    candidates = sorted(
        [
            *_numeric_markers(sentence),
            *_parenthetical_markers(sentence),
            *_narrative_markers(sentence),
        ],
        key=lambda marker: (marker.start, -marker.end),
    )
    markers: list[Marker] = []
    for marker in candidates:
        if markers and marker.start < markers[-1].end:
            continue
        markers.append(marker)
    return markers
