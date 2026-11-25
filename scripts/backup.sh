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

for database in citeproof citeproof_eval; do
  target="$BACKUP_DIR/$database-$STAMP.dump"
  pg_dump -Fc -d "$database" -f "$target.part"
  mv "$target.part" "$target"
  echo "đã sao lưu $database -> $(basename "$target")"
done

storage_target="$BACKUP_DIR/storage-$STAMP.tar.gz"
tar czf "$storage_target.part" -C "$STORAGE_DIR" .
mv "$storage_target.part" "$storage_target"
echo "đã sao lưu storage -> $(basename "$storage_target")"

find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name '*.dump' -o -name 'storage-*.tar.gz' -o -name '*.part' \) \
  -mtime "+$KEEP_DAYS" -delete
