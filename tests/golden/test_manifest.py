"""Kiểm tra bộ golden đầy đủ: mỗi đầu vào có kết quả mong đợi và TEI đã ghi."""

from tests.golden.cases import GOLDEN_DIR, GoldenCase, GoldenExpected, load_cases

ERROR_FIXTURES = {"encrypted", "image_only"}
EXPECTED_NAMES = {
    "vi_thesis_numeric",
    "en_paper_author_year",
    "vi_mixed_apa",
    "en_ieee",
    "vi_tcvn3_legacy",
    "vi_scanned_pages",
    "encrypted",
    "image_only",
    "vi_text_input",
    "multiline_refs",
}


def test_all_expected_inputs_present() -> None:
    assert {case.name for case in load_cases()} == EXPECTED_NAMES


def test_every_input_has_expected_and_tei() -> None:
    for case in load_cases():
        suffix = case.input_path.suffix
        if case.name in ERROR_FIXTURES:
            assert case.expected is None, case.name
            assert case.fulltext_tei is None, case.name
            continue
        assert case.expected is not None, case.name
        if suffix == ".pdf":
            assert case.fulltext_tei is not None, case.name
        else:
            assert case.citations_tei is not None, case.name


def test_expected_files_validate() -> None:
    expected_files = sorted(GOLDEN_DIR.glob("*.expected.json"))
    assert len(expected_files) == len(EXPECTED_NAMES - ERROR_FIXTURES)
    parsed = {
        path.name.removesuffix(".expected.json"): GoldenExpected.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        for path in expected_files
    }
    assert parsed["vi_tcvn3_legacy"].warnings == ["LEGACY_ENCODING"]
    assert parsed["vi_scanned_pages"].warnings == ["SCANNED_PAGES"]
    assert parsed["vi_scanned_pages"].page_count == 3


def test_citation_links_point_to_existing_labels() -> None:
    for case in load_cases():
        if case.expected is None:
            continue
        labels = {
            ref.label or str(index) for index, ref in enumerate(case.expected.references, start=1)
        }
        for citation in case.expected.citations:
            assert all(link is None or link in labels for link in citation.links), case.name


def test_golden_fixtures_are_parametrised(golden_case: GoldenCase) -> None:
    assert golden_case.expected is not None
    assert golden_case.input_path.exists()


def test_golden_docx_fixture_only_docx(golden_docx_case: GoldenCase) -> None:
    assert golden_docx_case.input_path.suffix == ".docx"
    assert golden_docx_case.citations_tei is not None
