"""Model miền dùng chung: ParsedReference và chuẩn hóa DOI (preflight F05)."""

import pytest

from citeproof.core.domain import ParsedReference, normalize_doi


@pytest.mark.parametrize(
    ("raw", "doi"),
    [
        ("https://doi.org/10.1000/ABC.", "10.1000/abc"),
        ("doi:10.18653/v1/N19-1423)", "10.18653/v1/n19-1423"),
        ("http://dx.doi.org/10.1109/CVPR.2016.90", "10.1109/cvpr.2016.90"),
        ("DOI: 10.48550/arXiv.1706.03762;", "10.48550/arxiv.1706.03762"),
        ("10.1/x", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_doi(raw: str | None, doi: str | None) -> None:
    assert normalize_doi(raw) == doi


def test_parsed_reference_defaults_are_independent() -> None:
    first, second = ParsedReference(), ParsedReference()
    first.authors.append("Nguyễn Văn An")
    assert second.authors == []
    assert ParsedReference.model_validate({"title": "T", "year": 2020}).year == 2020
