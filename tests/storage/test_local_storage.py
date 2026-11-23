from pathlib import Path

import pytest

from citeproof.storage import LocalStorage, document_prefix, validate_key


async def test_put_get_delete_roundtrip(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    await storage.put("documents/u/d/original.pdf", b"%PDF-1.7")
    assert await storage.get("documents/u/d/original.pdf") == b"%PDF-1.7"
    await storage.delete("documents/u/d/original.pdf")
    with pytest.raises(FileNotFoundError):
        await storage.get("documents/u/d/original.pdf")
    await storage.delete("documents/u/d/original.pdf")  # xóa lần hai không lỗi


async def test_delete_prefix_removes_tree(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    for key in ("a/b/1", "a/b/2", "a/c"):
        await storage.put(key, b"x")
    await storage.delete_prefix("a/b")
    assert await storage.get("a/c") == b"x"
    with pytest.raises(FileNotFoundError):
        await storage.get("a/b/1")
    await storage.delete_prefix("khong/co")  # tiền tố không tồn tại: không lỗi


async def test_list_children(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    await storage.put("documents/u/d2/y", b"y")
    await storage.put("documents/u/d1/x", b"x")
    assert await storage.list_children("documents/u") == ["d1", "d2"]
    assert await storage.list_children("documents/khac") == []


@pytest.mark.parametrize("key", ["../x", "a/../b", "/abs", "a//b", "a b", "", "a/", "a/./b"])
def test_validate_key_rejects(key: str) -> None:
    with pytest.raises(ValueError):
        validate_key(key)


async def test_storage_rejects_unsafe_key(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "root")
    with pytest.raises(ValueError):
        await storage.put("../escape", b"x")
    assert not (tmp_path / "escape").exists()


async def test_put_is_atomic_no_partial_file(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    await storage.put("a/b.bin", b"data")
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["b.bin"]


def test_document_prefix() -> None:
    import uuid

    u, d = uuid.uuid4(), uuid.uuid4()
    assert document_prefix(u, d) == f"documents/{u}/{d}"
