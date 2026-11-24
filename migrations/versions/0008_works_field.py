"""works: lĩnh vực OpenAlex cho cảnh báo ngoài lĩnh vực (QĐ-41)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("works", sa.Column("field", sa.Text(), nullable=True))
    # Dòng bài mẫu cũ lưu DOI như in (có chữ hoa): chuẩn hóa như dòng thật (`08` §4.4) để S3
    # tìm thấy và ghi đè được. Bỏ qua nếu đã có dòng mang DOI chuẩn hóa đó.
    op.execute(
        "UPDATE works SET doi = lower(doi) "
        "WHERE metadata_source = 'sample' AND doi <> lower(doi) "
        "AND NOT EXISTS (SELECT 1 FROM works AS other WHERE other.doi = lower(works.doi))"
    )


def downgrade() -> None:
    # DOI đã chuẩn hóa vẫn hợp lệ ở lược đồ cũ: không khôi phục chữ hoa.
    op.drop_column("works", "field")
