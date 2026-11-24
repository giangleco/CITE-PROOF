"""Bài mẫu cho môi trường local (spec web UI §7.8).

Ghi một tài liệu, một lần chạy COMPLETED và đủ kết quả vào chính các bảng mà pipeline
sẽ ghi, phủ mọi trạng thái tài liệu tham khảo và nhận định. Không làm bẩn tri thức dùng
chung: nguồn mẫu là `USER_UPLOAD` của chính người dùng (bị xóa cùng tài liệu), `works`
chỉ được tạo khi chưa có DOI đó và mang `metadata_source = "sample"`.

Đoạn bằng chứng là câu có thật trong tóm tắt các bài được trích.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.domain import normalize_doi
from citeproof.core.enums import (
    ClaimStatus,
    ContentLevel,
    ContentLevelUsed,
    DocumentKind,
    DomainFlag,
    EvidenceRelation,
    JobStatus,
    Language,
    LinkMethod,
    ParseMethod,
    ReferenceStatus,
    RefType,
    RunStatus,
    SourceOrigin,
    StageName,
    TitleMatch,
    UnverifiableReason,
    Verifier,
)
from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    ClaimVerification,
    Document,
    Evidence,
    Job,
    ReferenceCheck,
    ReferenceEntry,
    SourceChunk,
    SourceFile,
    VerificationRun,
    Work,
)
from citeproof.services.documents import SAMPLE_CODE

SAMPLE_METADATA_SOURCE = "sample"
SAMPLE_STORAGE_KEY = "sample/none"
TITLE = "Phát hiện tin giả tiếng Việt bằng mô hình ngôn ngữ huấn luyện trước"
ALL_SOURCES = ["crossref", "openalex", "semantic_scholar"]
LIMITATION = "Chỉ xét chính tài liệu được trích."


@dataclass(frozen=True)
class _Work:
    doi: str
    title: str
    authors: list[str]
    year: int
    venue: str


@dataclass(frozen=True)
class _Ref:
    raw: str
    ref_type: RefType
    status: ReferenceStatus
    parsed: dict[str, Any]
    work: _Work | None = None
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    chunks: tuple[str, ...] = ()


VASWANI = _Work(
    "10.48550/arXiv.1706.03762",
    "Attention Is All You Need",
    [
        "Ashish Vaswani",
        "Noam Shazeer",
        "Niki Parmar",
        "Jakob Uszkoreit",
        "Llion Jones",
        "Aidan N. Gomez",
        "Łukasz Kaiser",
        "Illia Polosukhin",
    ],
    2017,
    "Advances in Neural Information Processing Systems",
)
DEVLIN = _Work(
    "10.18653/v1/N19-1423",
    "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
    ["Jacob Devlin", "Ming-Wei Chang", "Kenton Lee", "Kristina Toutanova"],
    2019,
    "NAACL-HLT 2019",
)
PHOBERT = _Work(
    "10.18653/v1/2020.findings-emnlp.92",
    "PhoBERT: Pre-trained language models for Vietnamese",
    ["Dat Quoc Nguyen", "Anh Tuan Nguyen"],
    2020,
    "Findings of the Association for Computational Linguistics: EMNLP 2020",
)

Q_VASWANI_1 = (
    "We propose a new simple network architecture, the Transformer, based solely on attention "
    "mechanisms, dispensing with recurrence and convolutions entirely."
)
Q_VASWANI_2 = (
    "Experiments on two machine translation tasks show these models to be superior in quality "
    "while being more parallelizable and requiring significantly less time to train."
)
Q_DEVLIN = (
    "BERT is designed to pre-train deep bidirectional representations from unlabeled text by "
    "jointly conditioning on both left and right context in all layers."
)
Q_PHOBERT = (
    "Experimental results show that PhoBERT consistently outperforms the recent best "
    "pre-trained multilingual model XLM-R (Conneau et al., 2020) and improves the "
    "state-of-the-art in multiple Vietnamese-specific NLP tasks including Part-of-speech "
    "tagging, Dependency parsing, Named-entity recognition and Natural language inference."
)

REFS: tuple[_Ref, ...] = (
    _Ref(
        "A. Vaswani, N. Shazeer, N. Parmar, J. Uszkoreit, L. Jones, A. N. Gomez, Ł. Kaiser, and "
        "I. Polosukhin, “Attention is all you need,” in Advances in Neural Information "
        "Processing Systems, 2017.",
        RefType.CONFERENCE,
        ReferenceStatus.MATCHED,
        {"title": "Attention is all you need", "year": 2017},
        VASWANI,
        searched=["crossref"],
        chunks=(Q_VASWANI_1, Q_VASWANI_2),
    ),
    _Ref(
        "J. Devlin, M.-W. Chang, K. Lee, and K. Toutanova, “BERT: Pre-training of deep "
        "bidirectional transformers for language understanding,” 2018.",
        RefType.CONFERENCE,
        ReferenceStatus.METADATA_MISMATCH,
        {"title": "BERT: Pre-training of deep bidirectional transformers", "year": 2018},
        DEVLIN,
        mismatches=[
            {"field": "year", "in_document": "2018", "found": "2019"},
            {"field": "venue", "in_document": None, "found": "NAACL-HLT 2019"},
        ],
        searched=["crossref", "openalex"],
        chunks=(Q_DEVLIN,),
    ),
    _Ref(
        "D. Q. Nguyen and A. T. Nguyen, “PhoBERT: Pre-trained language models for "
        "Vietnamese,” in Findings of the Association for Computational Linguistics: "
        "EMNLP 2020, 2020.",
        RefType.CONFERENCE,
        ReferenceStatus.MATCHED,
        {"title": "PhoBERT: Pre-trained language models for Vietnamese", "year": 2020},
        PHOBERT,
        searched=["crossref"],
        chunks=(Q_PHOBERT,),
    ),
    _Ref(
        "Lê Thị Thu Hà, “Phát hiện tin giả trên mạng xã hội tiếng Việt bằng học sâu,” "
        "Luận văn thạc sĩ, 2021.",
        RefType.THESIS,
        ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES,
        {"title": "Phát hiện tin giả trên mạng xã hội tiếng Việt bằng học sâu", "year": 2021},
        searched=list(ALL_SOURCES),
    ),
    _Ref(
        "D. Jurafsky and J. H. Martin, Speech and Language Processing, 3rd ed. draft, 2023.",
        RefType.BOOK,
        ReferenceStatus.UNSUPPORTED_TYPE,
        {"title": "Speech and Language Processing", "year": 2023},
    ),
    _Ref(
        "Y. Liu et al., “RoBERTa: A robustly optimized BERT pretraining approach,” arXiv "
        "preprint arXiv:1907.11692, 2019.",
        RefType.PREPRINT,
        ReferenceStatus.LOOKUP_FAILED,
        {"title": "RoBERTa: A robustly optimized BERT pretraining approach", "year": 2019},
        failed=list(ALL_SOURCES),
    ),
    _Ref(
        "Tài liệu nội bộ nhóm nghiên cứu ;; bản thảo ,, 2019 tr. 3-7",
        RefType.UNKNOWN,
        ReferenceStatus.PARSE_FAILED,
        {},
    ),
)


@dataclass(frozen=True)
class _Claim:
    ref: int  # chỉ số trong REFS
    page: int
    section: str
    sentence: str
    span: str
    status: ClaimStatus
    needs_review: bool
    explanation: str
    reason: UnverifiableReason | None = None
    evidence: tuple[tuple[EvidenceRelation, str], ...] = ()


CLAIMS: tuple[_Claim, ...] = (
    _Claim(
        0,
        12,
        "2.3 Mô hình Transformer",
        "Kiến trúc Transformer loại bỏ hoàn toàn cơ chế hồi quy và tích chập, chỉ dựa vào cơ "
        "chế attention [1].",
        "loại bỏ hoàn toàn cơ chế hồi quy và tích chập, chỉ dựa vào cơ chế attention",
        ClaimStatus.SUPPORTED,
        False,
        "Nguồn nêu đúng điều này: Transformer chỉ dựa vào attention và bỏ hẳn hồi quy lẫn "
        "tích chập.",
        evidence=((EvidenceRelation.SUPPORTS, Q_VASWANI_1),),
    ),
    _Claim(
        2,
        18,
        "2.5 Mô hình ngôn ngữ cho tiếng Việt",
        "PhoBERT vượt trội so với mọi mô hình đa ngôn ngữ trên tất cả các tác vụ xử lý tiếng "
        "Việt [3].",
        "vượt trội so với mọi mô hình đa ngôn ngữ trên tất cả các tác vụ xử lý tiếng Việt",
        ClaimStatus.PARTIALLY_SUPPORTED,
        True,
        "Nguồn so với một mô hình đa ngôn ngữ cụ thể (XLM-R) trên bốn tác vụ được nêu tên. "
        "Nhận định của bài mở rộng thành “mọi mô hình” và “tất cả các tác vụ”, mạnh hơn "
        "điều nguồn cho phép.",
        evidence=((EvidenceRelation.PARTIALLY_SUPPORTS, Q_PHOBERT),),
    ),
    _Claim(
        1,
        15,
        "2.4 BERT và các biến thể",
        "BERT chỉ sử dụng ngữ cảnh bên trái khi huấn luyện trước [2].",
        "chỉ sử dụng ngữ cảnh bên trái khi huấn luyện trước",
        ClaimStatus.CONTRADICTED,
        True,
        "Nguồn nói BERT dùng đồng thời ngữ cảnh cả bên trái và bên phải, ngược với nhận định "
        "chỉ dùng bên trái.",
        evidence=((EvidenceRelation.CONTRADICTS, Q_DEVLIN),),
    ),
    _Claim(
        0,
        21,
        "3.1 Lựa chọn mô hình",
        "Transformer đã được chứng minh là hiệu quả nhất cho bài toán phát hiện tin giả [1].",
        "đã được chứng minh là hiệu quả nhất cho bài toán phát hiện tin giả",
        ClaimStatus.UNSUPPORTED,
        True,
        "Đã đọc toàn văn nhưng nguồn không bàn về phát hiện tin giả; thí nghiệm của nguồn là "
        "dịch máy. Đoạn gần nhất được hiển thị để bạn thấy nguồn thực sự nói gì.",
        evidence=((EvidenceRelation.RELATED, Q_VASWANI_2),),
    ),
    _Claim(
        3,
        9,
        "1.2 Các nghiên cứu liên quan",
        "Các mô hình học sâu cho kết quả tốt hơn học máy truyền thống trên dữ liệu tin giả "
        "tiếng Việt [4].",
        "cho kết quả tốt hơn học máy truyền thống trên dữ liệu tin giả tiếng Việt",
        ClaimStatus.UNVERIFIABLE,
        False,
        "Tài liệu được trích chưa tìm thấy trong các nguồn đã tra.",
        reason=UnverifiableReason.REFERENCE_NOT_FOUND,
    ),
    _Claim(
        4,
        11,
        "2.1 Mô hình ngôn ngữ",
        "Mô hình ngôn ngữ ước lượng xác suất của một chuỗi từ [5].",
        "ước lượng xác suất của một chuỗi từ",
        ClaimStatus.UNVERIFIABLE,
        False,
        "Chưa có toàn văn hay tóm tắt của nguồn.",
        reason=UnverifiableReason.NO_ACCESSIBLE_TEXT,
    ),
)


async def _get_or_create_work(session: AsyncSession, work: _Work, now: datetime) -> UUID:
    # DOI lưu dạng chuẩn hóa như dòng thật (`08` §4.4), để S3 tìm thấy và ghi đè dòng mẫu.
    doi = normalize_doi(work.doi)
    existing = await session.scalar(select(Work.id).where(Work.doi == doi))
    if existing is not None:
        return existing
    row = Work(
        doi=doi,
        title=work.title,
        title_norm=work.title.lower(),
        authors=work.authors,
        year=work.year,
        venue=work.venue,
        metadata_source=SAMPLE_METADATA_SOURCE,
        retrieved_at=now,
    )
    session.add(row)
    await session.flush()
    return row.id


async def seed_sample(
    session: AsyncSession, settings: Settings, *, user_id: UUID
) -> tuple[UUID, UUID]:
    """Tạo bài mẫu cho `user_id`; trả về (document_id, run_id). Không commit."""
    if settings.env != "local":
        raise InputError("Bài mẫu chỉ có ở môi trường local.", code="SAMPLE_LOCAL_ONLY")
    now = datetime.now(UTC)
    document = Document(
        user_id=user_id,
        kind=DocumentKind.TEXT.value,
        filename=TITLE,
        mime_type="application/json",
        storage_key=SAMPLE_STORAGE_KEY,
        sha256=f"sample-{uuid4().hex}",
        language=Language.VI.value,
        domain_flag=DomainFlag.IN_SCOPE.value,
        page_count=64,
        parse_warnings=[{"code": SAMPLE_CODE}, {"code": "SCANNED_PAGES", "pages": [41, 42, 43]}],
    )
    session.add(document)
    await session.flush()
    run = VerificationRun(
        document_id=document.id,
        user_id=user_id,
        status=RunStatus.COMPLETED.value,
        config={"variant": Verifier.V2.value},
        started_at=now,
        finished_at=now,
    )
    session.add(run)
    await session.flush()
    session.add_all(
        Job(run_id=run.id, stage=stage.value, status=JobStatus.DONE.value, attempts=1)
        for stage in StageName
    )

    entries: list[ReferenceEntry] = []
    chunk_ids: dict[str, UUID] = {}
    for index, ref in enumerate(REFS):
        entry = ReferenceEntry(
            document_id=document.id,
            order_index=index,
            raw_text=ref.raw,
            parsed={**ref.parsed, **({"doi": ref.work.doi} if ref.work else {})},
            ref_type=ref.ref_type.value,
            parse_method=ParseMethod.RULE.value,
            doi_normalized=normalize_doi(ref.work.doi) if ref.work else None,
        )
        session.add(entry)
        await session.flush()
        entries.append(entry)
        work_id = await _get_or_create_work(session, ref.work, now) if ref.work else None
        session.add(
            ReferenceCheck(
                run_id=run.id,
                reference_entry_id=entry.id,
                status=ref.status.value,
                work_id=work_id,
                mismatches=ref.mismatches,
                sources_searched=ref.searched,
                sources_failed=ref.failed,
                needs_user_review=ref.status != ReferenceStatus.MATCHED,
            )
        )
        if ref.chunks and ref.work:
            source = SourceFile(
                work_id=work_id,
                reference_entry_id=entry.id,
                owner_user_id=user_id,
                origin=SourceOrigin.USER_UPLOAD.value,
                access_status="USER_PROVIDED",
                content_level=ContentLevel.FULL_TEXT.value,
                url=f"https://doi.org/{ref.work.doi}",
                storage_key=SAMPLE_STORAGE_KEY,
                sha256=hashlib.sha256(ref.raw.encode()).hexdigest(),
                title_match=TitleMatch.MATCH.value,
                retrieved_at=now,
            )
            session.add(source)
            await session.flush()
            for chunk_index, text in enumerate(ref.chunks):
                chunk = SourceChunk(
                    source_file_id=source.id,
                    chunk_index=chunk_index,
                    text=text,
                    page=1,
                    section="Abstract",
                    char_start=0,
                    char_end=len(text),
                    region="BODY",
                    language=Language.EN.value,
                )
                session.add(chunk)
                await session.flush()
                chunk_ids[text] = chunk.id

    offset = 0
    for claim in CLAIMS:
        entry = entries[claim.ref]
        citation = Citation(
            document_id=document.id,
            marker_text=f"[{claim.ref + 1}]",
            sentence_text=claim.sentence,
            page=claim.page,
            char_start=offset,
            char_end=offset + len(claim.sentence),
            section=claim.section,
        )
        offset += len(claim.sentence) + 1
        session.add(citation)
        await session.flush()
        session.add(
            CitationLink(
                citation_id=citation.id,
                reference_entry_id=entry.id,
                link_method=LinkMethod.NUMERIC.value,
            )
        )
        claim_row = Claim(
            document_id=document.id,
            citation_id=citation.id,
            claim_text=claim.span,
            context_text=claim.sentence,
            language=Language.VI.value,
            extraction_method="sample",
        )
        session.add(claim_row)
        await session.flush()
        verification = ClaimVerification(
            run_id=run.id,
            claim_id=claim_row.id,
            reference_entry_id=entry.id,
            status=claim.status.value,
            unverifiable_reason=claim.reason.value if claim.reason else None,
            content_level_used=(
                ContentLevelUsed.FULL_TEXT.value if claim.evidence else ContentLevelUsed.NONE.value
            ),
            explanation=claim.explanation,
            limitations=LIMITATION,
            needs_user_review=claim.needs_review,
            model="sample",
            prompt_version="sample",
            schema_version="verdict/2",
            verifier=Verifier.V2.value,
        )
        session.add(verification)
        await session.flush()
        for relation, quote in claim.evidence:
            session.add(
                Evidence(
                    claim_verification_id=verification.id,
                    source_chunk_id=chunk_ids[quote],
                    relation=relation.value,
                    quote_text=quote,
                    char_start=0,
                    char_end=len(quote),
                )
            )
    await session.flush()
    return document.id, run.id
