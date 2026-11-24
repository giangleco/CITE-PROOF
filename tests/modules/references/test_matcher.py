"""Bộ so khớp tất định của S3 (spec §7.3, QĐ-20; Review Focus #4)."""

import unicodedata

import pytest

from citeproof.core.domain import ParsedReference
from citeproof.integrations.metadata import WorkAuthor, WorkRecord
from citeproof.modules.references.matcher import (
    CLOSE_DELTA,
    TITLE_ACCEPT,
    TITLE_EXACT,
    TITLE_WEAK,
    Mismatch,
    first_author_matches,
    match_candidate,
    pick_best,
    title_similarity,
)

HE_TITLE = "Deep Residual Learning for Image Recognition"
HE_AUTHORS = [WorkAuthor(family="He", given="Kaiming"), WorkAuthor(family="Zhang", given="Xiangyu")]
VI_TITLE = "Phân loại văn bản tiếng Việt bằng học sâu"


def _ref(**fields: object) -> ParsedReference:
    base: dict[str, object] = {
        "title": HE_TITLE,
        "authors": ["He, K.", "Zhang, X."],
        "year": 2016,
        "venue": "CVPR",
    }
    return ParsedReference.model_validate(base | fields)


def _record(**fields: object) -> WorkRecord:
    base: dict[str, object] = {
        "source": "crossref",
        "source_id": "10.1109/cvpr.2016.90",
        "title": HE_TITLE,
        "authors": HE_AUTHORS,
        "year": 2016,
        "venue": "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)",
        "doi": "10.1109/cvpr.2016.90",
    }
    return WorkRecord.model_validate(base | fields)


def test_thresholds_match_spec() -> None:
    assert (TITLE_ACCEPT, TITLE_WEAK, TITLE_EXACT, CLOSE_DELTA) == (90, 75, 95, 5.0)


def test_exact_match_is_matched() -> None:
    result = match_candidate(_ref(), _record())
    assert result.accepted
    assert result.mismatches == ()
    assert result.title_score == 100
    assert result.score == 1.0


def test_year_off_by_one_is_mismatch() -> None:
    result = match_candidate(_ref(year=2016), _record(year=2017))
    assert result.accepted
    assert result.mismatches == (Mismatch(field="year", in_document="2016", found="2017"),)


def test_doi_match_with_title_typo_flags_title() -> None:
    typo = "Deep Residual Networks for Image Recognition"
    result = match_candidate(
        _ref(title=typo, doi="https://doi.org/10.1109/CVPR.2016.90"), _record()
    )
    assert 85 <= result.title_score < TITLE_ACCEPT  # 88.6
    assert result.accepted
    assert result.score == 1.0  # chấp nhận qua DOI
    assert result.mismatches == (Mismatch(field="title", in_document=typo, found=HE_TITLE),)


def test_vietnamese_first_author_matches_folded() -> None:
    ref = ParsedReference(title=VI_TITLE, authors=["Nguyễn Văn An"], year=2020)
    crossref = WorkRecord(
        source="crossref",
        source_id="10.1000/vi",
        title=VI_TITLE,
        authors=[WorkAuthor(family="Nguyen", given="Van An")],
        year=2020,
    )
    assert match_candidate(ref, crossref).mismatches == ()
    # OpenAlex/Semantic Scholar đoán họ là từ cuối của tên hiển thị (ruling b của Task 8).
    openalex = crossref.model_copy(
        update={"source": "openalex", "authors": [WorkAuthor(family="An", given="Nguyễn Văn")]}
    )
    assert match_candidate(ref, openalex).mismatches == ()


def test_vietnamese_name_written_given_first_in_record() -> None:
    ref = ParsedReference(title=VI_TITLE, authors=["Nguyễn, Q. D."], year=2020)
    record = WorkRecord(
        source="semantic_scholar",
        source_id="p1",
        title=VI_TITLE,
        authors=[WorkAuthor(family="Nguyen", given="Dat Quoc")],
        year=2020,
    )
    assert match_candidate(ref, record).mismatches == ()


def test_different_first_author_is_mismatch() -> None:
    ref = ParsedReference(title=VI_TITLE, authors=["Trần Thị Bích"], year=2020)
    record = WorkRecord(
        source="crossref",
        source_id="10.1000/vi",
        title=VI_TITLE,
        authors=[WorkAuthor(family="Nguyen", given="Van An")],
        year=2020,
    )
    result = match_candidate(ref, record)
    assert result.accepted
    assert result.mismatches == (
        Mismatch(field="authors", in_document="Trần Thị Bích", found="Van An Nguyen"),
    )


def test_nfd_reference_matches_nfc_record() -> None:
    nfd_title = unicodedata.normalize("NFD", VI_TITLE)
    assert nfd_title != VI_TITLE
    ref = ParsedReference(
        title=nfd_title, authors=[unicodedata.normalize("NFD", "Nguyễn Văn An")], year=2020
    )
    record = WorkRecord(
        source="crossref",
        source_id="10.1000/vi",
        title=unicodedata.normalize("NFC", VI_TITLE),
        authors=[WorkAuthor(family="Nguyễn", given="Văn An")],
        year=2020,
    )
    result = match_candidate(ref, record)
    assert result.title_score == 100
    assert result.accepted
    assert result.mismatches == ()


def test_mixed_normalisation_author_matches() -> None:
    mixed = unicodedata.normalize("NFD", "Nguyễn") + " Văn An"
    assert first_author_matches(mixed, WorkAuthor(family="Nguyễn", given="Văn An"))


def test_weak_title_needs_author_and_year() -> None:
    near = "Deep Residual Learning in Image Classification"  # 77.8
    assert TITLE_WEAK <= title_similarity(HE_TITLE, near) < TITLE_ACCEPT
    record = _record(title=near, doi=None)
    assert match_candidate(_ref(year=2017), record).accepted
    assert not match_candidate(_ref(year=2018), record).accepted
    assert not match_candidate(_ref(year=None), record).accepted
    assert not match_candidate(_ref(authors=["Smith, J."]), record).accepted


def test_low_title_rejected() -> None:
    other = "Densely connected convolutional networks for image recognition"  # 64.6
    record = _record(title=other, doi="10.1109/cvpr.2017.243", authors=[WorkAuthor(family="Huang")])
    result = match_candidate(_ref(), record)
    assert 55 <= result.title_score < TITLE_WEAK
    assert not result.accepted
    assert result.score == pytest.approx(result.title_score / 100)


def test_different_doi_is_mismatch_when_accepted_by_title() -> None:
    result = match_candidate(_ref(doi="10.1000/wrong.1"), _record())
    assert result.accepted
    assert result.score == pytest.approx(result.title_score / 100)
    assert result.mismatches == (
        Mismatch(field="doi", in_document="10.1000/wrong.1", found="10.1109/cvpr.2016.90"),
    )


def test_venue_and_missing_fields_not_reported() -> None:
    ref = _ref(venue="NeurIPS", year=None, authors=[])
    result = match_candidate(ref, _record(venue="Advances in Neural Information Processing"))
    assert result.accepted
    assert result.mismatches == ()


def test_missing_title_only_accepted_by_doi() -> None:
    no_title = _ref(title=None)
    assert not match_candidate(no_title, _record(doi=None)).accepted
    by_doi = match_candidate(no_title.model_copy(update={"doi": "10.1109/cvpr.2016.90"}), _record())
    assert by_doi.accepted
    assert by_doi.title_score == 0
    # Bài không ghi tiêu đề: không phải lệch tiêu đề.
    assert by_doi.mismatches == ()


def test_close_call_needs_review() -> None:
    first = _record(title="Deep residual learning for visual recognition", doi=None)  # 92.7
    second = _record(title="Deep Residual Networks for Image Recognition", doi=None)  # 88.6
    best = pick_best(_ref(), [second, first])
    assert best is not None
    assert best.record is first
    assert best.result.title_score - match_candidate(_ref(), second).title_score < CLOSE_DELTA
    assert best.close_call


def test_clear_winner_is_not_close_call() -> None:
    other = _record(title="Deep Residual Learning in Image Classification", doi=None)  # 77.8
    best = pick_best(_ref(), [other, _record()])
    assert best is not None
    assert best.result.title_score == 100
    assert not best.close_call


def test_preprint_and_published_version_prefer_matching_year() -> None:
    preprint = _record(source="openalex", year=2015, doi="10.48550/arxiv.1512.03385")
    published = _record(source="openalex", year=2016)
    best = pick_best(_ref(), [preprint, published])
    assert best is not None
    assert best.record is published
    # Bản preprint lệch năm so với bài: không phải ứng viên mơ hồ.
    assert not best.close_call


def test_pick_best_none_when_nothing_accepted() -> None:
    other = _record(title="Going deeper with convolutions", authors=[WorkAuthor(family="Szegedy")])
    assert pick_best(_ref(), [other]) is None
    assert pick_best(_ref(), []) is None
