"""Chuỗi tra cứu của S3 (spec §7.3, QĐ-19): thứ tự nguồn, trạng thái, nguồn đã tra/lỗi.

Gateway thật trên connector giả (không HTTP) và cache trong bộ nhớ.
"""

import uuid
from typing import Any

import pytest

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import ReferenceStatus, RefType
from citeproof.core.errors import ExternalServiceError, InternalError
from citeproof.integrations.metadata import (
    SOURCES,
    InMemoryMetadataCache,
    MetadataGateway,
    WorkAuthor,
    WorkRecord,
)
from citeproof.modules.references.cascade import (
    CascadeResolver,
    CascadeResult,
    ReferenceEntryView,
    ReferenceResolver,
)

HE_TITLE = "Deep Residual Learning for Image Recognition"
HE_DOI = "10.1109/cvpr.2016.90"

Result = WorkRecord | list[WorkRecord] | Exception | None


class FakeConnector:
    """Connector giả: kết quả cố định cho tra DOI và tìm kiếm, ghi lại mọi lời gọi."""

    def __init__(self, name: str, *, doi: Result = None, search: Result = None) -> None:
        self.name = name
        self._doi = doi
        self._search = [] if search is None else search
        self.calls: list[tuple[str, Any]] = []

    async def get_by_doi(self, doi: str) -> WorkRecord | None:
        self.calls.append(("doi", doi))
        if isinstance(self._doi, Exception):
            raise self._doi
        assert not isinstance(self._doi, list)
        return self._doi

    async def search(self, ref: ParsedReference, limit: int = 5) -> list[WorkRecord]:
        self.calls.append(("search", ref.title))
        if isinstance(self._search, Exception):
            raise self._search
        assert isinstance(self._search, list)
        return self._search

    async def search_text(
        self, query: str, *, year: int | None = None, limit: int = 5
    ) -> list[WorkRecord]:
        raise AssertionError("S3 không dùng search_text")


async def _no_sleep(seconds: float) -> None:
    return None


def _gateway(**connectors: FakeConnector) -> MetadataGateway:
    full = {name: connectors.get(name) or FakeConnector(name) for name in SOURCES}
    return MetadataGateway(full, InMemoryMetadataCache(), {}, breaker_threshold=3, sleep=_no_sleep)


def _he(source: str = "crossref", **fields: object) -> WorkRecord:
    base: dict[str, object] = {
        "source": source,
        "source_id": HE_DOI,
        "title": HE_TITLE,
        "authors": [WorkAuthor(family="He", given="Kaiming")],
        "year": 2016,
        "doi": HE_DOI,
    }
    return WorkRecord.model_validate(base | fields)


def _entry(ref_type: RefType = RefType.CONFERENCE, **fields: object) -> ReferenceEntryView:
    base: dict[str, object] = {"title": HE_TITLE, "authors": ["He, K."], "year": 2016}
    parsed = ParsedReference.model_validate(base | fields)
    return ReferenceEntryView(id=uuid.uuid4(), raw_text="[1] He…", parsed=parsed, ref_type=ref_type)


def _error() -> ExternalServiceError:
    return ExternalServiceError("Nguồn metadata lỗi.", code="METADATA_HTTP_ERROR")


async def test_resolver_satisfies_protocol() -> None:
    resolver: ReferenceResolver = CascadeResolver(_gateway())
    result = await resolver.resolve(_entry(title=None), None)
    assert isinstance(result, CascadeResult)


async def test_all_sources_fail_gives_lookup_failed() -> None:
    connectors = {name: FakeConnector(name, search=_error()) for name in SOURCES}
    result = await CascadeResolver(_gateway(**connectors)).resolve(_entry(), None)
    assert result.status is ReferenceStatus.LOOKUP_FAILED
    assert result.sources_searched == ()
    assert result.sources_failed == ("crossref", "openalex", "semantic_scholar")
    assert result.record is None and result.match is None
    assert not result.needs_user_review


async def test_not_found_lists_only_successful_sources() -> None:
    crossref = FakeConnector("crossref", search=_error())
    result = await CascadeResolver(_gateway(crossref=crossref)).resolve(_entry(), None)
    assert result.status is ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES
    assert result.sources_searched == ("openalex", "semantic_scholar")
    assert result.sources_failed == ("crossref",)


async def test_doi_lookup_first() -> None:
    crossref = FakeConnector("crossref", doi=_he(), search=[_he()])
    openalex = FakeConnector("openalex", doi=_he("openalex"), search=[_he("openalex")])
    gateway = _gateway(crossref=crossref, openalex=openalex)
    result = await CascadeResolver(gateway).resolve(_entry(doi="doi:10.1109/CVPR.2016.90"), None)
    assert crossref.calls == [("doi", HE_DOI)]
    assert openalex.calls == []
    assert result.status is ReferenceStatus.MATCHED
    assert result.record is not None and result.record.source == "crossref"
    assert result.match is not None and result.match.score == 1.0
    assert result.sources_searched == ("crossref",)
    assert result.sources_failed == ()


async def test_doi_not_in_crossref_then_openalex_by_doi() -> None:
    crossref = FakeConnector("crossref", doi=None)
    openalex = FakeConnector("openalex", doi=_he("openalex", year=2017))
    s2 = FakeConnector("semantic_scholar")
    gateway = _gateway(crossref=crossref, openalex=openalex, semantic_scholar=s2)
    result = await CascadeResolver(gateway).resolve(_entry(doi=HE_DOI), None)
    assert [call[0] for call in crossref.calls + openalex.calls] == ["doi", "doi"]
    assert s2.calls == []
    assert result.status is ReferenceStatus.METADATA_MISMATCH
    assert result.match is not None
    assert [(m.field, m.in_document, m.found) for m in result.match.mismatches] == [
        ("year", "2016", "2017")
    ]
    assert result.sources_searched == ("crossref", "openalex")


async def test_doi_not_found_falls_back_to_search_in_order() -> None:
    crossref = FakeConnector("crossref", doi=None, search=[_he(title="Going deeper")])
    openalex = FakeConnector("openalex", doi=None, search=[_he("openalex", doi=None)])
    s2 = FakeConnector("semantic_scholar", search=[_he("semantic_scholar")])
    gateway = _gateway(crossref=crossref, openalex=openalex, semantic_scholar=s2)
    result = await CascadeResolver(gateway).resolve(_entry(doi="10.1000/not.registered"), None)
    assert [c[0] for c in crossref.calls] == ["doi", "search"]
    assert [c[0] for c in openalex.calls] == ["doi", "search"]
    assert s2.calls == []  # dừng ở nguồn đầu tiên có ứng viên được chấp nhận
    assert result.status is ReferenceStatus.MATCHED
    assert result.record is not None and result.record.source == "openalex"
    assert result.sources_searched == ("crossref", "openalex")


async def test_doi_without_title_not_found_skips_search() -> None:
    crossref = FakeConnector("crossref", doi=None)
    openalex = FakeConnector("openalex", doi=None)
    gateway = _gateway(crossref=crossref, openalex=openalex)
    result = await CascadeResolver(gateway).resolve(_entry(title=None, doi=HE_DOI), None)
    assert [c[0] for c in crossref.calls + openalex.calls] == ["doi", "doi"]
    assert result.status is ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES
    # Semantic Scholar không được tra (không tìm kiếm khi bài không ghi tiêu đề).
    assert result.sources_searched == ("crossref", "openalex")


async def test_doi_without_title_lookup_errors_give_lookup_failed() -> None:
    crossref = FakeConnector("crossref", doi=_error())
    openalex = FakeConnector("openalex", doi=_error())
    gateway = _gateway(crossref=crossref, openalex=openalex)
    result = await CascadeResolver(gateway).resolve(_entry(title=None, doi=HE_DOI), None)
    assert result.status is ReferenceStatus.LOOKUP_FAILED
    assert result.sources_searched == ()
    assert result.sources_failed == ("crossref", "openalex")


async def test_source_with_one_successful_query_counts_as_searched() -> None:
    # Crossref tra DOI được (không thấy) nhưng lỗi khi tìm kiếm: đã tra một phần, cũng là lỗi.
    crossref = FakeConnector("crossref", doi=None, search=_error())
    gateway = _gateway(crossref=crossref, openalex=FakeConnector("openalex", doi=None))
    result = await CascadeResolver(gateway).resolve(_entry(doi=HE_DOI), None)
    assert result.status is ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES
    assert result.sources_searched == ("crossref", "openalex", "semantic_scholar")
    assert result.sources_failed == ("crossref",)


async def test_matching_source_counts_as_searched_after_doi_errors() -> None:
    crossref = FakeConnector("crossref", doi=_error(), search=[_he()])
    openalex = FakeConnector("openalex", doi=_error())
    gateway = _gateway(crossref=crossref, openalex=openalex)
    result = await CascadeResolver(gateway).resolve(_entry(doi=HE_DOI), None)
    assert result.status is ReferenceStatus.MATCHED
    assert result.record is not None and result.record.source == "crossref"
    assert result.sources_searched == ("crossref",)  # PRD §4.5.2: MATCHED kèm nguồn đã tra
    assert result.sources_failed == ("crossref", "openalex")


async def test_parse_failed_without_title_and_doi() -> None:
    connectors = {name: FakeConnector(name) for name in SOURCES}
    result = await CascadeResolver(_gateway(**connectors)).resolve(
        _entry(title=None, doi="not-a-doi"), None
    )
    assert result.status is ReferenceStatus.PARSE_FAILED
    assert result.sources_searched == result.sources_failed == ()
    assert all(not c.calls for c in connectors.values())


@pytest.mark.parametrize("ref_type", [RefType.WEB, RefType.THESIS, RefType.OTHER])
async def test_unsupported_type_without_doi_not_looked_up(ref_type: RefType) -> None:
    connectors = {name: FakeConnector(name) for name in SOURCES}
    result = await CascadeResolver(_gateway(**connectors)).resolve(_entry(ref_type), None)
    assert result.status is ReferenceStatus.UNSUPPORTED_TYPE
    assert result.sources_searched == result.sources_failed == ()
    assert all(not c.calls for c in connectors.values())


async def test_unsupported_type_with_doi_is_looked_up() -> None:
    crossref = FakeConnector("crossref", doi=_he())
    result = await CascadeResolver(_gateway(crossref=crossref)).resolve(
        _entry(RefType.THESIS, doi=HE_DOI), None
    )
    assert result.status is ReferenceStatus.MATCHED
    assert crossref.calls == [("doi", HE_DOI)]


async def test_close_call_sets_needs_user_review() -> None:
    near = _he(title="Deep residual learning for visual recognition", doi=None)  # 92.7
    nearer = _he(title="Deep Residual Networks for Image Recognition", doi=None)  # 88.6
    crossref = FakeConnector("crossref", search=[near, nearer])
    result = await CascadeResolver(_gateway(crossref=crossref)).resolve(_entry(), None)
    assert result.status is ReferenceStatus.METADATA_MISMATCH
    assert result.close_call and result.needs_user_review


async def test_breaker_open_source_is_failed() -> None:
    crossref = FakeConnector("crossref", search=_error())
    gateway = _gateway(crossref=crossref)
    resolver = CascadeResolver(gateway)
    for _ in range(3):
        await resolver.resolve(_entry(title=f"Tiêu đề {uuid.uuid4()}"), None)
    assert not gateway.available("crossref")
    calls = len(crossref.calls)
    result = await resolver.resolve(_entry(), None)
    assert len(crossref.calls) == calls  # ngắt mạch: không gọi nguồn
    assert result.sources_failed == ("crossref",)
    assert result.status is ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES


async def test_programming_error_propagates() -> None:
    crossref = FakeConnector("crossref", search=InternalError("lỗi lập trình"))
    with pytest.raises(InternalError):
        await CascadeResolver(_gateway(crossref=crossref)).resolve(_entry(), None)


async def test_doi_hit_with_unrelated_title_is_mismatch_for_review() -> None:
    bert = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    crossref = FakeConnector("crossref", doi=_he(title=bert))
    result = await CascadeResolver(_gateway(crossref=crossref)).resolve(_entry(doi=HE_DOI), None)
    assert result.status is ReferenceStatus.METADATA_MISMATCH
    assert result.record is not None and result.record.doi == HE_DOI  # vẫn liên kết bản ghi
    assert result.needs_user_review
    assert not result.close_call
    assert result.match is not None
    assert [(m.field, m.found) for m in result.match.mismatches] == [("title", bert)]


async def test_short_title_contained_in_other_work_continues_cascade() -> None:
    longer = _he(
        title="Attention mechanisms for neural machine translation",
        doi=None,
        year=2019,
        authors=[WorkAuthor(family="Bahdanau")],
    )
    crossref = FakeConnector("crossref", search=[longer])
    openalex = FakeConnector("openalex", search=[longer])
    gateway = _gateway(crossref=crossref, openalex=openalex)
    entry = _entry(title="Attention mechanisms", year=2015)
    result = await CascadeResolver(gateway).resolve(entry, None)
    assert result.status is ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES
    assert result.sources_searched == SOURCES
    matching_year = await CascadeResolver(
        _gateway(crossref=FakeConnector("crossref", search=[longer]))
    ).resolve(_entry(title="Attention mechanisms", year=2019), None)
    # Được chấp nhận (năm trùng); tác giả đầu khác nên là METADATA_MISMATCH.
    assert matching_year.record is not None
    assert matching_year.status is ReferenceStatus.METADATA_MISMATCH
    assert matching_year.match is not None
    assert [m.field for m in matching_year.match.mismatches] == ["authors"]
