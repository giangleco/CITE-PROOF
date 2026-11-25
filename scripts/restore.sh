#!/usr/bin/env bash
# Khôi phục từ bản sao lưu (chạy trên máy chủ, thư mục gốc repo).
#   scripts/restore.sh <citeproof.dump> <storage.tar.gz> [citeproof_eval.dump]
# Dừng app/worker, nạp lại DB và storage, rồi khởi động lại. Ghi đè dữ liệu hiện tại.
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "dùng: $0 <citeproof.dump> <storage.tar.gz> [citeproof_eval.dump]" >&2
  exit 2
fi

DUMP="$(realpath "$1")"
STORAGE="$(realpath "$2")"
EVAL_DUMP=""
if [ "$#" -eq 3 ]; then
  EVAL_DUMP="$(realpath "$3")"
fi
for file in "$DUMP" "$STORAGE" ${EVAL_DUMP:+"$EVAL_DUMP"}; do
  [ -f "$file" ] || { echo "không thấy tệp: $file" >&2; exit 2; }
done

COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.production)

"${COMPOSE[@]}" up -d db
"${COMPOSE[@]}" stop app worker

"${COMPOSE[@]}" exec -T db pg_restore -U citeproof -d citeproof --clean --if-exists \
  --no-owner < "$DUMP"
if [ -n "$EVAL_DUMP" ]; then
  "${COMPOSE[@]}" exec -T db pg_restore -U citeproof -d citeproof_eval --clean --if-exists \
    --no-owner < "$EVAL_DUMP"
fi

"${COMPOSE[@]}" run --rm --no-deps --user root --entrypoint sh \
  -v "$STORAGE:/restore/storage.tar.gz:ro" app -c \
  'find /app/var/storage -mindepth 1 -delete \
   && tar xzf /restore/storage.tar.gz -C /app/var/storage \
   && chown -R 1000:1000 /app/var/storage'

"${COMPOSE[@]}" up -d app worker
echo "đã khôi phục xong"
