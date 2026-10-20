# The check container: Python 3.11 + uv + Node 22 + git, for the gate and the PostgreSQL tests (scripts/check-container/*.sh).
FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /bin/uv
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp UV_CACHE_DIR=/tmp/uvcache npm_config_cache=/tmp/npmcache
WORKDIR /work
