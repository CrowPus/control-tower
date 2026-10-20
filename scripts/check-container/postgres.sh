#!/bin/sh
# The backend tests on a throwaway PostgreSQL, in the capped check container (production shares this machine: never
# without a cap). Extra pytest arguments pass through:  scripts/check-container/postgres.sh -k team
set -eu
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CACHE="${CT_CHECK_CACHE:-$HOME/.cache/ct-check}"
IMAGE="${CT_CHECK_IMAGE:-ct-check}"
NET="ct-test-net-$$"; DB="ct-test-pg-$$"
mkdir -p "$CACHE"
docker network create "$NET" >/dev/null
trap 'docker rm -f "$DB" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --name "$DB" --network "$NET" --memory=1g --memory-swap=1g \
  -e POSTGRES_USER=tower -e POSTGRES_PASSWORD=tower -e POSTGRES_DB=tower postgres:16-alpine >/dev/null
until docker exec "$DB" pg_isready -U tower -d tower >/dev/null 2>&1; do sleep 1; done
sleep 2
docker run --rm --network "$NET" --memory=3g --memory-swap=3g --cpus=2 --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e TEST_DATABASE_URL="postgresql://tower:tower@$DB:5432/tower" \
  -v "$REPO:/work" -v "$CACHE:/cache" -w /work/backend "$IMAGE" .venv/bin/pytest -q -p no:cacheprovider "$@"
