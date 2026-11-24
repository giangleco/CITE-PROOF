import copy
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field, HttpUrl

from citeproof.llm.schema import strict_output_schema
from citeproof.llm.types import OutputModel

_UNSUPPORTED = {
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


class Inner(BaseModel):
    note: str = Field(max_length=300)
    score: int = Field(ge=1, le=10)


class Outer(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "outer/1"
    items: list[Inner] = Field(max_length=4)
    code: str = Field(pattern=r"^[A-Z]+$", min_length=1)
    ratio: float = Field(gt=0, lt=1, multiple_of=0.5)
    tags: set[str] = Field(default_factory=set)
    label: str = "x"


class Formats(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "formats/1"
    when: datetime
    url: HttpUrl
    path: Path


class KeywordNames(OutputModel):
    """Tên trường trùng từ khoá JSON Schema vẫn phải giữ nguyên."""

    SCHEMA_VERSION: ClassVar[str] = "keywords/1"
    pattern: str
    default: str
    format: str
    minimum: int


def _walk(node: Any) -> list[dict[str, Any]]:
    """Mọi dict trong cây (để kiểm tra không còn khoá nào bị cấm ở bất kỳ đâu)."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


def test_strict_schema_strips_unsupported() -> None:
    original = Outer.model_json_schema()
    keys_before = {k for d in _walk(original) for k in d}
    assert {"maxLength", "minimum", "maximum", "maxItems", "pattern", "default"} <= keys_before

    strict = strict_output_schema(original)

    for node in _walk(strict):
        assert not (_UNSUPPORTED & node.keys()), node
    inner = strict["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert inner["properties"]["note"] == {"title": "Note", "type": "string"}
    assert inner["properties"]["score"] == {"title": "Score", "type": "integer"}
    assert strict["additionalProperties"] is False
    assert strict["properties"]["items"]["items"] == {"$ref": "#/$defs/Inner"}
    assert set(strict["required"]) == set(original["required"])


def test_strict_schema_keeps_only_supported_formats() -> None:
    strict = strict_output_schema(Formats.model_json_schema())
    props = strict["properties"]
    assert props["when"]["format"] == "date-time"
    assert props["url"] == {"format": "uri", "title": "Url", "type": "string"}
    assert "format" not in props["path"]


def test_property_names_matching_keywords_are_kept() -> None:
    strict = strict_output_schema(KeywordNames.model_json_schema())
    assert set(strict["properties"]) == {"pattern", "default", "format", "minimum"}
    assert set(strict["required"]) == {"pattern", "default", "format", "minimum"}


def test_strict_schema_is_a_deep_copy() -> None:
    original = Outer.model_json_schema()
    snapshot = copy.deepcopy(original)
    strict = strict_output_schema(original)
    strict["$defs"]["Inner"]["properties"]["note"]["title"] = "changed"
    strict["required"].append("changed")
    assert original == snapshot


def test_every_object_schema_gets_additional_properties_false() -> None:
    schema = {
        "type": "object",
        "properties": {
            "maybe": {"anyOf": [{"type": "object", "properties": {}}, {"type": "null"}]},
            "free": {"type": "object", "additionalProperties": True},
        },
    }
    strict = strict_output_schema(schema)
    assert strict["additionalProperties"] is False
    assert strict["properties"]["maybe"]["anyOf"][0]["additionalProperties"] is False
    assert strict["properties"]["free"]["additionalProperties"] is False
