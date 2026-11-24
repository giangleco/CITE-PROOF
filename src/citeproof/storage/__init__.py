"""Lưu file theo khóa (T-202, phần local).

Khóa luôn do hệ thống tạo từ UUID (`document_prefix`), không bao giờ từ tên file
của người dùng; `validate_key` chặn mọi khóa có thể thoát khỏi thư mục gốc.
"""

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from citeproof.core.settings import Settings

_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")


def validate_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key) or any(part in {".", ".."} for part in key.split("/")):
        raise ValueError("khóa lưu trữ không hợp lệ")
    return key


def document_prefix(user_id: UUID, document_id: UUID) -> str:
    return f"documents/{user_id}/{document_id}"


class Storage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...

    async def list_children(self, prefix: str) -> list[str]: ...


class LocalStorage:
    """Lưu dưới một thư mục gốc; ghi qua file tạm rồi đổi tên để không để lại file dở."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        return self._root / validate_key(key)

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Tên tạm riêng cho mỗi lần ghi: hai worker cùng ghi một khóa dùng chung
            # (`sources/open/<sha256>.pdf`) không ghi đè file tạm của nhau.
            tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def delete_prefix(self, prefix: str) -> None:
        await asyncio.to_thread(shutil.rmtree, self._path(prefix), True)

    async def list_children(self, prefix: str) -> list[str]:
        path = self._path(prefix)

        def children() -> list[str]:
            if not path.is_dir():
                return []
            return sorted(child.name for child in path.iterdir())

        return await asyncio.to_thread(children)


def make_storage(settings: Settings) -> Storage:
    return LocalStorage(settings.storage_dir)
