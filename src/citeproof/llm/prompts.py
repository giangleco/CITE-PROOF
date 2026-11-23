"""Nạp và render prompt template có phiên bản (docs/07a §4.8.1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from citeproof.llm.context import PromptParts

USER_SEPARATOR = "=== USER ==="


@dataclass(frozen=True)
class PromptTemplate:
    task: str
    version: str
    system: str
    user_template: str


class PromptLoader:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[tuple[str, str], PromptTemplate] = {}
        self._env = Environment(
            undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True
        )

    def load(self, task: str, version: str) -> PromptTemplate:
        key = (task, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self._root / task / f"{version}.md"
        if not path.is_file():
            raise FileNotFoundError(f"prompt template not found: {path}")
        # Chuẩn hoá xuống dòng tường minh: file CRLF/CR cho đúng prompt như file LF.
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.splitlines(keepends=True)
        idx = [i for i, ln in enumerate(lines) if ln.rstrip("\n") == USER_SEPARATOR]
        if len(idx) != 1:
            raise ValueError(
                f"{path}: expected exactly one {USER_SEPARATOR!r} line, found {len(idx)}"
            )
        system = "".join(lines[: idx[0]]).removesuffix("\n")
        user = "".join(lines[idx[0] + 1 :])
        tpl = PromptTemplate(task=task, version=version, system=system, user_template=user)
        self._cache[key] = tpl
        return tpl

    def render(self, task: str, version: str, **variables: Any) -> PromptParts:
        tpl = self.load(task, version)
        user = self._env.from_string(tpl.user_template).render(**variables)
        return PromptParts(task=task, prompt_version=version, system=tpl.system, user=user)
