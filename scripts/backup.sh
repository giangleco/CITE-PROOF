#!/usr/bin/env bash
# Sao lưu DB (citeproof, citeproof_eval) và volume storage vào /backups; xóa bản cũ.
# Chạy trong service `backup` (PGHOST/PGUSER/PGPASSWORD từ môi trường). Không in bí mật.
set -euo pipefail
# Bản sao lưu chứa tài liệu người dùng: chỉ chủ sở hữu (root trong container) đọc được.
umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"
STORAGE_DIR="${STORAGE_DIR:-/storage}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

# Storage trước: không phụ thuộc kết quả pg_dump, nên tệp người dùng luôn được sao lưu.
storage_target="$BACKUP_DIR/storage-$STAMP.tar.gz"
tar czf "$storage_target.part" -C "$STORAGE_DIR" .
mv "$storage_target.part" "$storage_target"
echo "đã sao lưu storage -> $(basename "$storage_target")"

# `citeproof_eval` chỉ có khi docker/db/init chạy trên volume mới: bỏ qua nếu chưa có.
for database in citeproof citeproof_eval; do
  if [ "$database" != citeproof ]; then
    exists="$(psql -d citeproof -tAc "select 1 from pg_database where datname='$database'")"
    if [ "$exists" != 1 ]; then
      echo "bỏ qua $database: chưa có trong máy chủ"
      continue
    fi
  fi
  target="$BACKUP_DIR/$database-$STAMP.dump"
  pg_dump -Fc -d "$database" -f "$target.part"
  mv "$target.part" "$target"
  echo "đã sao lưu $database -> $(basename "$target")"
done

find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name '*.dump' -o -name 'storage-*.tar.gz' -o -name '*.part' \) \
  -mtime "+$KEEP_DAYS" -delete
