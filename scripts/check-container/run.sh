#!/bin/sh
# Run one command in the memory-capped check container (when the machine runs anything else, never without a cap).
#   scripts/check-container/run.sh 'cd backend && .venv/bin/pytest -q'
# Build the image once: docker build --memory=2g -t ct-check scripts/check-container
# First time in a fresh checkout: run.sh 'npm ci --include=dev && cd backend && uv sync --locked --all-extras'
set -eu
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CACHE="${CT_CHECK_CACHE:-$HOME/.cache/ct-check}"
IMAGE="${CT_CHECK_IMAGE:-ct-check}"
mkdir -p "$CACHE"
exec docker run --rm --memory=3g --memory-swap=3g --cpus=2 --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e UV_CACHE_DIR=/cache/uv -e npm_config_cache=/cache/npm -e UV_PROJECT_ENVIRONMENT=/work/backend/.venv \
  -v "$REPO:/work" -v "$CACHE:/cache" -w /work "$IMAGE" sh -c "$1"
