"""JSON schema gửi cho structured output của API (spec §8.1).

API chưa hỗ trợ các ràng buộc số, độ dài, pattern, `default` và phần lớn `format`;
bản gửi đi bỏ chúng, còn `LLMClient` vẫn validate đủ bằng Pydantic.
"""

import copy
from collections.abc import Mapping
from typing import Any

_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "default",
    }
)
_SUPPORTED_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
# Vị trí chứa schema con; mọi khoá khác (enum, const, required, ...) là dữ liệu, chỉ sao chép.
_NAMED_SUBSCHEMAS = frozenset({"properties", "patternProperties", "$defs", "definitions"})
_LIST_SUBSCHEMAS = frozenset({"anyOf", "allOf", "oneOf", "prefixItems"})
_SINGLE_SUBSCHEMAS = frozenset(
    {"items", "additionalProperties", "not", "contains", "if", "then", "else"}
)


def strict_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Bản sao sâu của `schema` chỉ còn những gì API structured output chấp nhận.

    Bỏ ràng buộc không hỗ trợ, bỏ `format` ngoài danh sách hỗ trợ, đặt
    `additionalProperties: false` cho mọi object (kể cả trong `$defs`). Tên trường
    trùng từ khoá (ví dụ trường tên `pattern`) được giữ nguyên.
    """
    return _strict_node(schema)


def _strict_node(node: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_KEYWORDS:
            continue
        if key == "format" and not (isinstance(value, str) and value in _SUPPORTED_FORMATS):
            continue
        out[key] = _strict_value(key, value)
    if _is_object(out):
        out["additionalProperties"] = False
    return out


def _strict_value(key: str, value: Any) -> Any:
    if key in _NAMED_SUBSCHEMAS and isinstance(value, Mapping):
        return {name: _strict_subschema(sub) for name, sub in value.items()}
    if key in _LIST_SUBSCHEMAS and isinstance(value, list):
        return [_strict_subschema(sub) for sub in value]
    if key in _SINGLE_SUBSCHEMAS:
        if isinstance(value, list):  # `items` dạng tuple của draft cũ
            return [_strict_subschema(sub) for sub in value]
        return _strict_subschema(value)
    return copy.deepcopy(value)


def _strict_subschema(value: Any) -> Any:
    """Schema con là dict, hoặc boolean schema (`true`/`false`) thì giữ nguyên."""
    if isinstance(value, Mapping):
        return _strict_node(value)
    return copy.deepcopy(value)


def _is_object(node: Mapping[str, Any]) -> bool:
    kind = node.get("type")
    return kind == "object" or (isinstance(kind, list) and "object" in kind) or "properties" in node
