#!/usr/bin/env bash
# Update this install to the newest version (or the one you name): a backup first, then the new version; when it does
# not become healthy within 3 minutes, the old version and the backup are put back.
#
#   ./scripts/update.sh            the newest version of the branch you are on
#   ./scripts/update.sh v1.2.0     a release tag
set -euo pipefail
# This file changes when the version does: run from a copy, so the update never reads half of an old and half of a new script.
if [ -z "${CT_UPDATE_ROOT:-}" ]; then
  CT_UPDATE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  copy="$(mktemp)"
  cp "$0" "$copy"
  CT_UPDATE_ROOT="$CT_UPDATE_ROOT" exec bash "$copy" "$@"
fi
trap 'rm -f "$0"' EXIT
cd "$CT_UPDATE_ROOT"

[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "The code here has local changes (git status): commit or undo them first." >&2; exit 1; }
OLD="$(git rev-parse HEAD)"
echo "== Backup"
./scripts/backup.sh
BACKUP="$(find backups -maxdepth 1 -type d -name 'ct-*' | sort | tail -n 1)"
echo "== New version"
git fetch --tags --quiet || echo "(Could not reach the code's origin; using what is here.)"
if [ -n "${1:-}" ]; then git checkout --quiet "$1"; else git pull --ff-only --quiet; fi
NEW="$(git rev-parse HEAD)"
[ "$NEW" != "$OLD" ] || { echo "Already the newest version ($NEW)."; exit 0; }

healthy() {
  for _ in $(seq 1 90); do
    [ "$(docker compose ps app --format '{{.Health}}')" = "healthy" ] && [ "$(docker compose ps worker --format '{{.Health}}')" = "healthy" ] && return 0
    sleep 2
  done
  return 1
}
# `up` itself fails when the server never becomes healthy (the worker waits for it): that is the case handled below.
if docker compose up -d --build && healthy; then
  echo "Updated: $OLD -> $NEW. The backup from before is $BACKUP."
  exit 0
fi
echo "== The new version did not start; going back to $OLD and the backup $BACKUP" >&2
docker compose logs --tail 40 app worker >&2 || true
git checkout --quiet "$OLD"
docker compose build app worker
./scripts/restore.sh "$BACKUP" --yes
if healthy; then echo "Back on $OLD with the data from before the update." >&2; else echo "Still not healthy: see 'docker compose logs app worker'." >&2; fi
exit 1
