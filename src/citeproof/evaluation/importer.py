"""Nhập bộ dữ liệu đánh giá thành các dòng thật (spec §11; 07a TK-15).

Claim (D1-D4): dưới người dùng hệ thống `eval@citeproof.local`, một tài liệu TEXT cho mỗi lần
nhập, mỗi work được trích một `reference_entries` + `works` (gộp theo DOI → arXiv → tiêu đề) +
`reference_checks` `MATCHED`, nguồn `OPEN_ACCESS` là tệp `.txt` dùng chung
(`sources/open/<sha256>.txt`), mỗi claim một `citations` + `citation_links` + `claims`. Lần chạy
có job S1-S5 `DONE` (`{"skipped": true}`) và S6 trong hàng đợi, nên S6-S9 chạy bằng đúng mã sản
phẩm.

Tham khảo (D5): một tài liệu TEXT chứa các chuỗi tham khảo; lần chạy bắt đầu từ S1, S4-S8 bỏ
qua (S2, S3 chạy thật). Mọi ghi DB nằm trong một giao dịch; nội dung lưu vào storage trước đó.
"""

import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.core.enums import (
    AccessStatus,
    ContentLevel,
    DocumentKind,
    Language,
    LinkMethod,
    ParseMethod,
    ReferenceStatus,
    RefType,
    SourceOrigin,
    StageName,
    TitleMatch,
)
from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    Document,
    ReferenceCheck,
    ReferenceEntry,
    SourceFile,
    User,
    Work,
)
from citeproof.evaluation.dataset import CitedWork, ClaimItem, Dataset, ReferenceItem
from citeproof.modules.references.normalize import title_norm
from citeproof.modules.sources.fetch import open_source_key
from citeproof.pipeline.config import RunConfig
from citeproof.pipeline.runs import create_seeded_run
from citeproof.storage import Storage, document_prefix

__all__ = [
    "EVAL_METADATA_SOURCE",
    "EVAL_USER_EMAIL",
    "ImportResult",
    "RefImportResult",
    "import_claims",
    "import_references",
]

EVAL_USER_EMAIL = "eval@citeproof.local"
EVAL_METADATA_SOURCE = "eval"
EXTRACTION_METHOD = "eval"
# Mật khẩu không bao giờ khớp (không phải chuỗi argon2): người dùng hệ thống không đăng nhập được.
_NO_PASSWORD = "!"
_TEXT_MIME = "application/json"
_CLAIM_SKIPS = frozenset({StageName.S1, StageName.S2, StageName.S3, StageName.S4, StageName.S5})
_REFERENCE_SKIPS = frozenset({StageName.S4, StageName.S5, StageName.S6, StageName.S7, StageName.S8})
_ARXIV_PREFIX = re.compile(r"^(arxiv:)", re.IGNORECASE)
_ARXIV_VERSION = re.compile(r"v\d+$")
_ACCESS = {
    ContentLevel.FULL_TEXT: AccessStatus.OPEN_FULL_TEXT,
    ContentLevel.ABSTRACT: AccessStatus.OPEN_ABSTRACT,
}


@dataclass(frozen=True)
class ImportResult:
    run_id: UUID
    claim_ids: dict[str, UUID]


@dataclass(frozen=True)
class RefImportResult:
    run_id: UUID
    document_id: UUID


def normalize_arxiv(value: str | None) -> str | None:
    """ID arXiv không tiền tố `arXiv:` và không phiên bản (`2101.00001v2` → `2101.00001`)."""
    if value is None or not value.strip():
        return None
    return _ARXIV_VERSION.sub("", _ARXIV_PREFIX.sub("", value.strip())).lower()


@dataclass(frozen=True)
class _WorkKey:
    doi: str | None
    arxiv_id: str | None
    title: str

    @property
    def key(self) -> str:
        if self.doi:
            return f"doi:{self.doi}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        return f"title:{title_norm(self.title)}"

    def raw_reference(self) -> str:
        parts = [self.title.rstrip(".") + "."]
        if self.arxiv_id:
            parts.append(f"arXiv:{self.arxiv_id}.")
        if self.doi:
            parts.append(f"https://doi.org/{self.doi}")
        return " ".join(parts)


def _work_key(work: CitedWork) -> _WorkKey:
    doi = normalize_doi(work.doi)
    arxiv = normalize_arxiv(work.arxiv_id)
    title = (work.title or "").strip() or (f"arXiv:{arxiv}" if arxiv else f"doi:{doi}")
    return _WorkKey(doi=doi, arxiv_id=arxiv, title=title)


def _document_language(items: Sequence[ClaimItem]) -> str:
    languages = {item.claim_language for item in items}
    return languages.pop() if len(languages) == 1 else Language.MIXED.value


async def _eval_user(session: AsyncSession) -> UUID:
    await session.execute(
        insert(User)
        .values(
            email=EVAL_USER_EMAIL,
            password_hash=_NO_PASSWORD,
            display_name="Evaluation",
            consent_llm_at=func.now(),
        )
        .on_conflict_do_nothing(index_elements=["email"])
    )
    user_id: UUID = (
        await session.execute(select(User.id).where(User.email == EVAL_USER_EMAIL))
    ).scalar_one()
    return user_id


async def _find_or_create_work(session: AsyncSession, key: _WorkKey) -> UUID:
    """Work có sẵn theo DOI → arXiv → tiêu đề (tiêu đề chỉ với work của bộ đánh giá)."""
    lookups = []
    if key.doi:
        lookups.append(Work.doi == key.doi)
    if key.arxiv_id:
        lookups.append(Work.arxiv_id == key.arxiv_id)
    if not key.doi and not key.arxiv_id:
        lookups.append(
            (Work.title_norm == title_norm(key.title))
            & (Work.metadata_source == EVAL_METADATA_SOURCE)
        )
    for condition in lookups:
        found = await session.scalar(
            select(Work.id).where(condition).order_by(Work.created_at, Work.id).limit(1)
        )
        if found is not None:
            return found
    work_id: UUID = (
        await session.execute(
            insert(Work)
            .values(
                doi=key.doi,
                arxiv_id=key.arxiv_id,
                title=key.title,
                title_norm=title_norm(key.title),
                metadata_source=EVAL_METADATA_SOURCE,
            )
            .returning(Work.id)
        )
    ).scalar_one()
    return work_id


async def _source_file(
    session: AsyncSession, *, work_id: UUID, level: ContentLevel, sha: str
) -> None:
    """Nguồn mở dùng chung của work; đã có (cùng work, cùng nội dung) thì giữ dòng cũ."""
    await session.execute(
        insert(SourceFile)
        .values(
            work_id=work_id,
            origin=SourceOrigin.OPEN_ACCESS.value,
            access_status=_ACCESS[level].value,
            content_level=level.value,
            storage_key=open_source_key(sha, "txt"),
            sha256=sha,
            title_match=TitleMatch.MATCH.value,
        )
        .on_conflict_do_nothing(
            index_elements=["work_id", "sha256"],
            index_where=SourceFile.origin == SourceOrigin.OPEN_ACCESS.value,
        )
    )


async def _create_document(
    session: AsyncSession,
    storage: Storage,
    *,
    user_id: UUID,
    name: str,
    body: str,
    references: str,
    language: str,
) -> UUID:
    document_id = uuid.uuid4()
    data = json.dumps({"body": body, "references": references}, ensure_ascii=False).encode()
    key = f"{document_prefix(user_id, document_id)}/original.json"
    await storage.put(key, data)
    session.add(
        Document(
            id=document_id,
            user_id=user_id,
            kind=DocumentKind.TEXT.value,
            filename=f"{name}.json",
            mime_type=_TEXT_MIME,
            storage_key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            language=language,
        )
    )
    await session.flush()
    return document_id


def _sources(items: Sequence[ClaimItem]) -> dict[tuple[str, ContentLevel], bytes]:
    """Nội dung nguồn theo (work, mức nội dung); hai nội dung khác nhau cho cùng chỗ là lỗi."""
    sources: dict[tuple[str, ContentLevel], bytes] = {}
    for item in items:
        if item.source_text is None:
            continue
        slot = (_work_key(item.cited_work).key, item.source_content_level)
        data = unicodedata.normalize("NFC", item.source_text).encode()
        if sources.setdefault(slot, data) != data:
            raise InputError(
                f"Mục {item.id}: cùng work và mức nội dung nhưng nguồn khác mục trước.",
                code="EVAL_SOURCE_CONFLICT",
            )
    return sources


async def import_claims(
    session_factory: async_sessionmaker[AsyncSession],
    storage: Storage,
    settings: Settings,
    dataset: Dataset[ClaimItem],
    config: RunConfig,
    *,
    split: str | None,
) -> ImportResult:
    """Nhập claim của `split` (None: mọi mục) và tạo lần chạy bắt đầu từ S6."""
    items = [item for item in dataset.items if split is None or item.split == split]
    if not items:
        raise InputError(f"Không có mục nào thuộc split {split!r}.", code="EVAL_EMPTY_SPLIT")
    sources = _sources(items)
    source_sha: dict[tuple[str, ContentLevel], str] = {}
    for slot, data in sources.items():
        sha = hashlib.sha256(data).hexdigest()
        await storage.put(open_source_key(sha, "txt"), data)
        source_sha[slot] = sha

    works: dict[str, _WorkKey] = {}
    for item in items:
        cited = _work_key(item.cited_work)
        works.setdefault(cited.key, cited)
    numbers = {key: index for index, key in enumerate(works, start=1)}

    # Thân bài: mỗi claim một đoạn, kết thúc bằng marker số của work được trích.
    paragraphs: list[str] = []
    spans: list[tuple[int, int, str]] = []
    offset = 0
    for item in items:
        context = " ".join((item.citing_context or item.claim_text).split())
        marker = f"[{numbers[_work_key(item.cited_work).key]}]"
        paragraph = f"{context} {marker}"
        start = offset + len(context) + 1
        spans.append((start, start + len(marker), marker))
        paragraphs.append(paragraph)
        offset += len(paragraph) + 2
    references = "\n".join(f"[{n}] {works[key].raw_reference()}" for key, n in numbers.items())

    async with session_factory() as session:
        user_id = await _eval_user(session)
        document_id = await _create_document(
            session,
            storage,
            user_id=user_id,
            name=dataset.name,
            body="\n\n".join(paragraphs),
            references=references,
            language=_document_language(items),
        )
        run_id = await create_seeded_run(
            session,
            document_id=document_id,
            user_id=user_id,
            config=config.model_dump(mode="json"),
            start=StageName.S6,
            skipped=_CLAIM_SKIPS,
        )
        entry_ids: dict[str, UUID] = {}
        for key, number in numbers.items():
            work = works[key]
            work_id = await _find_or_create_work(session, work)
            entry_id: UUID = (
                await session.execute(
                    insert(ReferenceEntry)
                    .values(
                        document_id=document_id,
                        order_index=number - 1,
                        raw_text=work.raw_reference(),
                        parsed=ParsedReference(
                            title=work.title, doi=work.doi, label=str(number)
                        ).model_dump(mode="json"),
                        ref_type=RefType.ARTICLE.value,
                        parse_method=ParseMethod.RULE.value,
                        doi_normalized=work.doi,
                    )
                    .returning(ReferenceEntry.id)
                )
            ).scalar_one()
            entry_ids[key] = entry_id
            session.add(
                ReferenceCheck(
                    run_id=run_id,
                    reference_entry_id=entry_id,
                    status=ReferenceStatus.MATCHED.value,
                    work_id=work_id,
                    mismatches=[],
                    match_score=1,
                )
            )
            for (slot_key, level), sha in source_sha.items():
                if slot_key == key:
                    await _source_file(session, work_id=work_id, level=level, sha=sha)

        claim_ids: dict[str, UUID] = {}
        for item, (start, end, marker), paragraph in zip(items, spans, paragraphs, strict=True):
            citation = Citation(
                document_id=document_id,
                marker_text=marker,
                sentence_text=paragraph,
                page=None,
                char_start=start,
                char_end=end,
            )
            session.add(citation)
            await session.flush()
            session.add(
                CitationLink(
                    citation_id=citation.id,
                    reference_entry_id=entry_ids[_work_key(item.cited_work).key],
                    link_method=LinkMethod.NUMERIC.value,
                )
            )
            claim = Claim(
                document_id=document_id,
                citation_id=citation.id,
                claim_text=item.claim_text,
                context_text=item.citing_context or item.claim_text,
                language=item.claim_language,
                extraction_method=EXTRACTION_METHOD,
            )
            session.add(claim)
            await session.flush()
            claim_ids[item.id] = claim.id
        await session.commit()
    return ImportResult(run_id=run_id, claim_ids=claim_ids)


async def import_references(
    session_factory: async_sessionmaker[AsyncSession],
    storage: Storage,
    dataset: Dataset[ReferenceItem],
    config: RunConfig,
) -> RefImportResult:
    """Một tài liệu TEXT chứa các chuỗi tham khảo `[n] …`; lần chạy S1-S3 (S4-S8 bỏ qua)."""
    references = "\n".join(
        f"[{n}] {' '.join(item.raw_reference.split())}"
        for n, item in enumerate(dataset.items, start=1)
    )
    async with session_factory() as session:
        user_id = await _eval_user(session)
        document_id = await _create_document(
            session,
            storage,
            user_id=user_id,
            name=dataset.name,
            body="Reference list imported for evaluation.",
            references=references,
            language=Language.UNKNOWN.value,
        )
        run_id = await create_seeded_run(
            session,
            document_id=document_id,
            user_id=user_id,
            config=config.model_dump(mode="json"),
            start=StageName.S1,
            skipped=_REFERENCE_SKIPS,
        )
        await session.commit()
    return RefImportResult(run_id=run_id, document_id=document_id)
