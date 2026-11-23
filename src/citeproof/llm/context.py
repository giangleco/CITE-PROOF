"""ID ngắn, data block và ContextBuilder (docs/07a §4.8.1, §4.8.3, §4.6.1)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, TypeVar
from uuid import UUID

InT_contra = TypeVar("InT_contra", contravariant=True)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class UnknownShortIdError(LookupError):
    """ID ngắn không tồn tại trong IdMap (thường do LLM bịa ra)."""

    def __init__(self, short_id: str, prefix: str) -> None:
        super().__init__(f"unknown short id {short_id!r} for prefix {prefix!r}")
        self.short_id = short_id
        self.prefix = prefix


class IdMap:
    """Ánh xạ UUID <-> ID ngắn `<prefix>_<n>` theo thứ tự thêm vào."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._by_uuid: dict[UUID, str] = {}
        self._by_short: dict[str, UUID] = {}

    def add(self, target: UUID) -> str:
        existing = self._by_uuid.get(target)
        if existing is not None:
            return existing
        short = f"{self.prefix}_{len(self._by_uuid) + 1}"
        self._by_uuid[target] = short
        self._by_short[short] = target
        return short

    def resolve(self, short_id: str) -> UUID:
        try:
            return self._by_short[short_id]
        except KeyError:
            raise UnknownShortIdError(short_id, self.prefix) from None

    def items(self) -> list[tuple[str, UUID]]:
        return list(self._by_short.items())


def escape_data(text: str) -> str:
    """Escape `&` trước, rồi `<`, `>` để dữ liệu không đóng được data block."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _check_name(name: str, what: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid {what} name: {name!r}")


def render_data_block(
    tag: str,
    body: str,
    *,
    attrs: Mapping[str, str] | None = None,
    untrusted: bool = True,
) -> str:
    _check_name(tag, "tag")
    parts = [tag]
    for name, value in (attrs or {}).items():
        _check_name(name, "attribute")
        parts.append(f'{name}="{escape_data(value).replace(chr(34), "&quot;")}"')
    if untrusted:
        parts.append('untrusted="true"')
    return f"<{' '.join(parts)}>\n{escape_data(body)}\n</{tag}>"


@dataclass(frozen=True)
class PromptParts:
    task: str
    prompt_version: str
    system: str
    user: str
    id_maps: Mapping[str, IdMap] = field(default_factory=dict)


class ContextBuilder(Protocol[InT_contra]):
    task: str
    prompt_version: str

    def build(self, inp: InT_contra) -> PromptParts: ...
