#!/usr/bin/env bash
# Put a backup back: the database and the install's files (and studio videos, if the backup has them).
# Everything since that backup is lost. The settings (.env) are NOT replaced; the backup's copy is in its folder.
#
#   ./scripts/restore.sh backups/ct-20261020-030000 [--yes]
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${1:?which backup? e.g. ./scripts/restore.sh backups/ct-20261020-030000}"
[ -f "$DIR/db.dump" ] && [ -f "$DIR/data.tar.gz" ] || { echo "$DIR is not a backup (db.dump and data.tar.gz missing)." >&2; exit 1; }
if [ "${2:-}" != "--yes" ]; then
  read -r -p "Replace everything with the backup $DIR? Everything since then is lost. Type 'restore': " answer
  [ "$answer" = "restore" ] || { echo "Nothing changed."; exit 1; }
fi
docker compose stop app worker
docker compose up -d db redis
# The whole schema is emptied first: tables a newer version added (and the backup does not know) must go too.
docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U tower -d tower -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose exec -T db pg_restore -U tower -d tower --no-owner --exit-on-error < "$DIR/db.dump"
docker compose run --rm --no-deps -T --entrypoint sh app -c 'find /data -mindepth 1 -delete && tar xzf - -C /data' < "$DIR/data.tar.gz"
[ -f "$DIR/marketing.tar.gz" ] && docker compose run --rm --no-deps -T --entrypoint sh app -c 'find /marketing -mindepth 1 -delete && tar xzf - -C /marketing' < "$DIR/marketing.tar.gz"
docker compose up -d || true
echo "Restored $DIR (version $(cat "$DIR/version" 2>/dev/null || echo unknown))."
