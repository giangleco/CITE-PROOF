"""Giao thức embedding cho chunk nguồn (ADR-007, QĐ-02).

v1 không dùng embedding: `NoEmbedder` trả None nên S6 để `source_chunks.embedding` và
`embedding_model` NULL, còn S7 chỉ truy xuất lexical. Khi SPK-04 chọn được model, chỉ cần
một `Embedder` thật trả mỗi văn bản một vector (cùng thứ tự đầu vào, số chiều của cột
`embedding`) và đặt nó vào `PipelineDeps.embedder`.
"""

from collections.abc import Sequence
from typing import Protocol


class Embedder(Protocol):
    # Tên model ghi vào `source_chunks.embedding_model`; None khi không tạo embedding.
    model: str | None

    async def embed(self, texts: Sequence[str]) -> list[list[float]] | None: ...


class NoEmbedder:
    """Không tạo embedding (mặc định của v1)."""

    model: str | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        return None
