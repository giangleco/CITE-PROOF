"""Ghi phản hồi TEI thật của GROBID cho bộ golden (T-208); chỉ chạy tay, CI không chạy.

    docker compose up -d grobid
    uv run python scripts/record_grobid_fixtures.py [--grobid-url http://127.0.0.1:8070]

PDF (trừ `encrypted`, `image_only`) -> `<name>.fulltext.tei.xml` qua `processFulltextDocument`;
DOCX/JSON -> `<name>.citations.tei.xml` qua `processCitationList` với chuỗi tham khảo thô lấy
từ `RAW_REFERENCES` của `make_golden_fixtures.py`. Tham số giống `GrobidClient` (Task 6).
"""

import argparse
import sys
from pathlib import Path

import httpx
from make_golden_fixtures import DEFAULT_OUT, RAW_REFERENCES

SKIP_PDF = {"encrypted", "image_only"}
FULLTEXT_DATA: dict[str, str | list[str]] = {
    "segmentSentences": "1",
    "teiCoordinates": ["p", "s", "head", "ref", "biblStruct", "figure"],
    "includeRawCitations": "1",
    "consolidateHeader": "0",
    "consolidateCitations": "0",
}


def record_fulltext(client: httpx.Client, base: str, pdf: Path, out: Path) -> None:
    response = client.post(
        f"{base}/api/processFulltextDocument",
        data=FULLTEXT_DATA,
        files={"input": (pdf.name, pdf.read_bytes(), "application/pdf")},
    )
    response.raise_for_status()
    out.write_text(response.text, encoding="utf-8")


def record_citations(client: httpx.Client, base: str, raw: list[str], out: Path) -> None:
    response = client.post(
        f"{base}/api/processCitationList",
        data={"includeRawCitations": "1", "consolidateCitations": "0", "citations": raw},
        headers={"Accept": "application/xml"},
    )
    response.raise_for_status()
    out.write_text(response.text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grobid-url", default="http://127.0.0.1:8070")
    parser.add_argument("--dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    base = args.grobid_url.rstrip("/")
    with httpx.Client(timeout=300.0) as client:
        try:
            alive = client.get(f"{base}/api/isalive")
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"Không gọi được GROBID tại {base}: chạy `docker compose up -d grobid`."
            ) from exc
        if alive.status_code != 200:
            raise SystemExit(f"GROBID chưa sẵn sàng (HTTP {alive.status_code}).")
        for pdf in sorted(args.dir.glob("*.pdf")):
            if pdf.stem in SKIP_PDF:
                continue
            out = args.dir / f"{pdf.stem}.fulltext.tei.xml"
            record_fulltext(client, base, pdf, out)
            print(f"{out.name}: {out.stat().st_size} byte")
        for name, raw in RAW_REFERENCES.items():
            out = args.dir / f"{name}.citations.tei.xml"
            record_citations(client, base, raw, out)
            print(f"{out.name}: {out.stat().st_size} byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
