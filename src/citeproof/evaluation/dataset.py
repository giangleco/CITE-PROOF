"""Bộ dữ liệu đánh giá JSONL (13 §B.3) và cấu hình biến thể YAML (spec §11).

`load_claims` đọc D1-D4 (mỗi dòng một `ClaimItem`), `load_references` đọc D5. Hash là SHA-256
của đúng các byte của tệp, dùng cho namespace cache `eval:<tên>@<hash>` và manifest. Nguồn của
claim là `source_text` ngay trong dòng hoặc `source_path` tương đối với thư mục của bộ dữ liệu
(được đọc sẵn vào `source_text`). Lỗi chỉ nêu số dòng, không nêu nội dung.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from citeproof.core.enums import ClaimStatus, ContentLevel, ReferenceStatus, Verifier
from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.pipeline.config import RunConfig

__all__ = [
    "CitedWork",
    "ClaimItem",
    "Dataset",
    "EvidenceSpan",
    "ReferenceItem",
    "load_claims",
    "load_references",
    "load_run_config",
]

# Khóa chỉ để mô tả cấu hình, không thuộc `RunConfig`.
_CONFIG_META_KEYS = frozenset({"name", "description"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(_Strict):
    page: int | None = None
    text: str = Field(min_length=1)


class CitedWork(_Strict):
    doi: str | None = None
    arxiv_id: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def _has_identity(self) -> Self:
        if not (self.doi or self.arxiv_id or self.title):
            raise ValueError("cited_work cần doi, arxiv_id hoặc title")
        return self


class _Item(_Strict):
    id: str = Field(min_length=1)


class ClaimItem(_Item):
    claim_text: str = Field(min_length=1)
    claim_language: Literal["en", "vi"]
    citing_context: str
    cited_work: CitedWork
    source_content_level: ContentLevel
    label: ClaimStatus
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    rationale: str | None = None
    annotator_pass: int | None = None
    annotated_at: date | None = None
    split: str
    origin: Literal["D1", "D2", "D3a", "D3b", "D4"]
    source_path: str | None = None
    source_text: str | None = None


class ReferenceItem(_Item):
    raw_reference: str = Field(min_length=1)
    expected_status: ReferenceStatus
    split: str
    origin: Literal["D5"]


@dataclass(frozen=True)
class Dataset[T]:
    name: str
    sha256: str
    items: list[T]


def _invalid(path: Path, line_no: int | None, what: str) -> InputError:
    where = f"dòng {line_no}" if line_no is not None else "tệp"
    return InputError(
        f"Bộ dữ liệu {path.name}: {where} không hợp lệ ({what}).", code="DATASET_INVALID"
    )


def _load[T: _Item](path: Path, model: type[T], prepare: Callable[[T, int], T]) -> Dataset[T]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError(f"Không đọc được bộ dữ liệu {path.name}.", code="DATASET_INVALID") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid(path, None, "không phải UTF-8") from exc
    items: list[T] = []
    seen: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = model.model_validate_json(line)
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in e["loc"]) or "(root)" for e in exc.errors()})
            raise _invalid(path, line_no, "trường " + ", ".join(fields)) from None
        if item.id in seen:
            raise _invalid(path, line_no, "id trùng")
        seen.add(item.id)
        items.append(prepare(item, line_no))
    if not items:
        raise _invalid(path, None, "không có mục nào")
    return Dataset(name=path.stem, sha256=hashlib.sha256(data).hexdigest(), items=items)


def load_claims(path: Path) -> Dataset[ClaimItem]:
    root = path.parent.resolve()

    def prepare(item: ClaimItem, line_no: int) -> ClaimItem:
        if item.source_path is None:
            return item
        if item.source_text is not None:
            raise _invalid(path, line_no, "chỉ được có một trong source_path, source_text")
        source = (root / item.source_path).resolve()
        if not source.is_relative_to(root):
            raise _invalid(path, line_no, "source_path ra ngoài thư mục bộ dữ liệu")
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _invalid(path, line_no, "không đọc được source_path") from exc
        return item.model_copy(update={"source_text": text})

    return _load(path, ClaimItem, prepare)


def load_references(path: Path) -> Dataset[ReferenceItem]:
    return _load(path, ReferenceItem, lambda item, _line: item)


def load_run_config(settings: Settings, path: Path) -> RunConfig:
    """`RunConfig` của biến thể: mặc định từ `settings` (như lần chạy thật), rồi khóa trong YAML."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InputError(f"Không đọc được cấu hình {path.name}.", code="CONFIG_INVALID") from exc
    if not isinstance(raw, dict):
        raise InputError(f"Cấu hình {path.name} phải là một bảng YAML.", code="CONFIG_INVALID")
    overrides = {k: v for k, v in raw.items() if k not in _CONFIG_META_KEYS}
    unknown = sorted(str(k) for k in overrides if k not in RunConfig.model_fields)
    if unknown or "demo" in overrides or "scope" in overrides:
        bad = ", ".join(unknown or ["demo/scope"])
        raise InputError(
            f"Cấu hình {path.name} có khóa không hỗ trợ: {bad}.", code="CONFIG_INVALID"
        )
    try:
        variant = Verifier(overrides.get("variant", Verifier.V2.value))
        merged = RunConfig.resolve(settings, variant=variant).model_dump(mode="json")
        for key, value in overrides.items():
            current = merged.get(key)
            # Bảng (model, effort, giới hạn…) chỉ ghi đè khóa được nêu.
            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = {**current, **value}
            else:
                merged[key] = value
        return RunConfig.model_validate(merged)
    except (ValueError, ValidationError) as exc:
        raise InputError(f"Cấu hình {path.name} không hợp lệ.", code="CONFIG_INVALID") from exc
