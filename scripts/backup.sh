#!/usr/bin/env bash
# A backup of this install: backups/ct-<time>/ with the database (db.dump), the install's files (data.tar.gz), the
# settings (env — it holds the secrets: keep backups as safe as .env) and the version. Studio videos only with --media.
#
#   ./scripts/backup.sh [--media]
#
# Backups older than BACKUP_KEEP_DAYS (in .env; default 14) are deleted. BACKUP_COPY_COMMAND (in .env), when set, is run
# with the new backup's folder as its argument — e.g.  BACKUP_COPY_COMMAND=rclone copy --max-age 2h backups remote:tower
set -euo pipefail
cd "$(dirname "$0")/.."
MEDIA=0; [ "${1:-}" = "--media" ] && MEDIA=1
env_get() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1; }
KEEP_DAYS="$(env_get BACKUP_KEEP_DAYS)"; KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
DIR="backups/ct-$STAMP"
umask 077
mkdir -p "$DIR"
docker compose exec -T db pg_dump -U tower -d tower --format=custom > "$DIR/db.dump"
docker compose exec -T app tar czf - -C /data . > "$DIR/data.tar.gz"
[ "$MEDIA" = 1 ] && docker compose exec -T app tar czf - -C /marketing . > "$DIR/marketing.tar.gz"
cp .env "$DIR/env"
git rev-parse HEAD > "$DIR/version" 2>/dev/null || echo unknown > "$DIR/version"
# A dump of a few hundred bytes means pg_dump failed quietly: never keep that as a good backup.
[ "$(wc -c < "$DIR/db.dump")" -gt 1000 ] || { rm -rf "$DIR"; echo "The database dump is empty: backup failed." >&2; exit 1; }
echo "$(date -u +%FT%TZ) backup $DIR ($(du -sh "$DIR" | cut -f1))"
COPY="$(env_get BACKUP_COPY_COMMAND)"
[ -n "$COPY" ] && sh -c "$COPY \"\$1\"" copy "$DIR"
find backups -maxdepth 1 -type d -name 'ct-*' -mtime +"$KEEP_DAYS" -exec rm -rf {} +
