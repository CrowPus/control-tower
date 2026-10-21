FROM node:22-bookworm-slim AS admin
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.admin.config.ts ./
COPY admin ./admin
RUN npm run build

FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --all-extras --no-dev
COPY backend/ ./
COPY --from=admin /app/dist-admin /app/dist-admin
# The install's Python plugins (docker-compose mounts ./plugins over it, so adding one needs no new image).
COPY plugins /app/plugins
# The connection kit the admin hands to a project's coding agent (Plugins → Connect your project).
COPY docs/CONNECT-A-PROJECT.md /app/docs/CONNECT-A-PROJECT.md
COPY tools/check_connection.py /app/tools/check_connection.py
# /data: the install's files; /marketing: the videos the studio made, their own volume.
RUN useradd --uid 10001 --create-home app && mkdir -p /data /marketing && chown app:app /data /marketing
USER app
ENV DATA_DIR=/data ADMIN_FRONTEND_DIST=/app/dist-admin MARKETING_DIR=/marketing PLUGINS_DIR=/app/plugins
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK CMD ["/app/backend/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/admin-api/health', timeout=3)"]
# Run the migrations first (app/prestart.py), then the server. The worker uses the same image with its own command.
CMD ["sh", "-c", "/app/backend/.venv/bin/python -m app.prestart && exec /app/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-server-header --timeout-graceful-shutdown 15"]
