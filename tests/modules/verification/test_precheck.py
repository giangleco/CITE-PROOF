"""Tiền kiểm tất định của S8 (spec §7.8): thứ tự và phạm vi theo biến thể."""

import uuid

import pytest

from citeproof.core.enums import (
    ContentLevel,
    ReferenceStatus,
    SourceOrigin,
    TitleMatch,
    UnverifiableReason,
    Verifier,
)
from citeproof.modules.retrieval.pairs import ClaimPair, SourceChoice, SourceRef
from citeproof.modules.verification.precheck import PairState, precheck

R = UnverifiableReason


def _pair(*, linked: bool = True, is_claim: bool = True) -> ClaimPair:
    return ClaimPair(
        claim_id=uuid.uuid4(),
        citation_id=uuid.uuid4(),
        reference_entry_id=uuid.uuid4() if linked else None,
        claim_text="c",
        context_text="",
        sentence_text="c [1].",
        language="vi",
        is_claim=is_claim,
        position=0,
    )


def _src(
    level: ContentLevel,
    *,
    chunks: int = 3,
    language: str | None = "en",
    origin: SourceOrigin = SourceOrigin.OPEN_ACCESS,
) -> SourceRef:
    return SourceRef(
        source_file_id=uuid.uuid4(),
        content_level=level,
        origin=origin,
        title_match=TitleMatch.UNKNOWN,
        chunk_count=chunks,
        language=language,
    )


FULL = _src(ContentLevel.FULL_TEXT)
ABSTRACT = _src(ContentLevel.ABSTRACT, chunks=1)
UPLOAD = _src(ContentLevel.FULL_TEXT, origin=SourceOrigin.USER_UPLOAD)


def _state(
    *,
    pair: ClaimPair | None = None,
    status: ReferenceStatus | None = ReferenceStatus.MATCHED,
    upload: bool = False,
    full: SourceRef | None = FULL,
    abstract: SourceRef | None = ABSTRACT,
) -> PairState:
    return PairState(
        pair=pair or _pair(),
        reference_status=status,
        has_upload=upload,
        sources=SourceChoice(full_text=full, abstract=abstract),
    )


def test_ready_pair_passes() -> None:
    for variant in (Verifier.V0, Verifier.V1, Verifier.V2):
        assert precheck(_state(), variant=variant, llm_configured=True) is None


def test_order_not_a_claim_first() -> None:
    state = _state(pair=_pair(linked=False, is_claim=False), full=None, abstract=None)
    assert precheck(state, variant=Verifier.V2, llm_configured=False) is R.NOT_A_CLAIM


def test_unresolved_before_reference_status() -> None:
    state = _state(pair=_pair(linked=False), status=None, full=None, abstract=None)
    assert precheck(state, variant=Verifier.V2, llm_configured=True) is R.REFERENCE_UNRESOLVED


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES, R.REFERENCE_NOT_FOUND),
        (ReferenceStatus.LOOKUP_FAILED, R.PROCESSING_ERROR),
    ],
)
def test_reference_status(status: ReferenceStatus, expected: UnverifiableReason) -> None:
    state = _state(status=status, full=None, abstract=None)
    assert precheck(state, variant=Verifier.V2, llm_configured=True) is expected
    # V0 cũng dừng ở trạng thái tài liệu tham khảo.
    assert precheck(state, variant=Verifier.V0, llm_configured=True) is expected


def test_own_upload_skips_reference_status() -> None:
    state = _state(status=ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES, upload=True, full=UPLOAD)
    assert precheck(state, variant=Verifier.V2, llm_configured=True) is None


def test_no_source() -> None:
    state = _state(full=None, abstract=None)
    assert precheck(state, variant=Verifier.V2, llm_configured=True) is R.NO_ACCESSIBLE_TEXT
    assert precheck(state, variant=Verifier.V0, llm_configured=True) is None


def test_v1_checks_abstract_only() -> None:
    state = _state(abstract=None)
    assert precheck(state, variant=Verifier.V1, llm_configured=True) is R.NO_ACCESSIBLE_TEXT
    assert precheck(state, variant=Verifier.V2, llm_configured=True) is None
    broken_full = _state(full=_src(ContentLevel.FULL_TEXT, chunks=0))
    assert precheck(broken_full, variant=Verifier.V1, llm_configured=True) is None


def test_fulltext_without_chunks() -> None:
    state = _state(full=_src(ContentLevel.FULL_TEXT, chunks=0))
    assert precheck(state, variant=Verifier.V2, llm_configured=True) is R.SOURCE_PARSE_FAILED


def test_v2_falls_back_to_abstract() -> None:
    state = _state(full=None)
    assert precheck(state, variant=Verifier.V2, llm_configured=True) is None
    empty_abstract = _state(full=None, abstract=_src(ContentLevel.ABSTRACT, chunks=0))
    assert (
        precheck(empty_abstract, variant=Verifier.V2, llm_configured=True) is R.SOURCE_PARSE_FAILED
    )


def test_source_language_unsupported() -> None:
    state = _state(full=_src(ContentLevel.FULL_TEXT, language="other"))
    assert (
        precheck(state, variant=Verifier.V2, llm_configured=True) is R.SOURCE_LANGUAGE_UNSUPPORTED
    )
    # V1 chỉ xét abstract (tiếng Anh).
    assert precheck(state, variant=Verifier.V1, llm_configured=True) is None
    assert precheck(state, variant=Verifier.V0, llm_configured=True) is None


def test_llm_not_configured_last() -> None:
    assert precheck(_state(), variant=Verifier.V2, llm_configured=False) is R.PROCESSING_ERROR
    assert precheck(_state(), variant=Verifier.V0, llm_configured=False) is R.PROCESSING_ERROR
    no_source = _state(full=None, abstract=None)
    assert precheck(no_source, variant=Verifier.V2, llm_configured=False) is R.NO_ACCESSIBLE_TEXT
