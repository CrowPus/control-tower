#!/usr/bin/env bash
# Control Tower — install (or re-run to repair) on a server with Docker.
#
#   ./install.sh                                   asks for what it needs
#   ./install.sh --email you@example.com --name "Your Name" --address https://tower.example.com --time-zone Europe/Berlin --yes
#
# It makes .env with strong secrets (kept if they exist), the database's TLS certificate, builds and starts everything
# (server, worker, PostgreSQL, Redis), waits until it is healthy, and invites the first owner: it prints a one-time link
# where you set your password. Run it again at any time: it keeps your secrets and data, and invites an owner only when
# there is none.
#
# After it: open the link, set your password and your authenticator app, and follow "Getting started" in the admin.
set -euo pipefail
cd "$(dirname "$0")"

EMAIL="" NAME="" ADDRESS="" TZ_NAME="" COMPANY="" YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --email) EMAIL="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --address) ADDRESS="$2"; shift 2 ;;
    --time-zone) TZ_NAME="$2"; shift 2 ;;
    --company) COMPANY="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
ask() { # ask VAR "question" "default"
  local current="${!1}" answer
  if [ -n "$current" ]; then return; fi
  if [ "$YES" = 1 ] || [ ! -t 0 ]; then printf -v "$1" '%s' "$3"; return; fi
  read -r -p "$2 [${3}]: " answer
  printf -v "$1" '%s' "${answer:-$3}"
}
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -n 1 || true; }
env_set() { # env_set KEY VALUE — replace the line or add it; the value is written as is
  local tmp; tmp="$(mktemp)"
  if grep -q "^$1=" .env; then
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' .env > "$tmp"
  else
    cp .env "$tmp"; printf '%s=%s\n' "$1" "$2" >> "$tmp"
  fi
  cat "$tmp" > .env; rm -f "$tmp"
}
secret() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-"$1"; }

say "Checking this server"
command -v docker >/dev/null || fail "Docker is not installed: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing (the 'docker compose' command): https://docs.docker.com/compose/install/"
command -v openssl >/dev/null || fail "openssl is missing (apt install openssl)."
docker info >/dev/null 2>&1 || fail "Docker is not running, or this user may not use it (add it to the docker group, or run with sudo)."
echo "Docker, Compose and openssl are here."

say "Settings (.env)"
if [ ! -f .env ]; then cp .env.example .env; echo "Made .env from .env.example."; else echo "Using the .env that is here."; fi
chmod 600 .env
[ -n "$(env_get ENCRYPTION_SECRET)" ] || { env_set ENCRYPTION_SECRET "$(secret 48)"; echo "Made a new ENCRYPTION_SECRET. Keep .env safe: without it, stored keys cannot be read."; }
[ -n "$(env_get POSTGRES_PASSWORD)" ] || { env_set POSTGRES_PASSWORD "$(secret 32)"; echo "Made a new database password."; }

SYSTEM_TZ="$( (timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null) | head -n 1)"
TZ_DEFAULT="$(env_get TIME_ZONE)"
if [ -z "$TZ_DEFAULT" ] || [ "$TZ_DEFAULT" = "UTC" ]; then TZ_DEFAULT="${SYSTEM_TZ:-UTC}"; fi
ask TZ_NAME "Your time zone (agents' schedules and Insights' days)" "$TZ_DEFAULT"
[ -e "/usr/share/zoneinfo/$TZ_NAME" ] || [ "$TZ_NAME" = "UTC" ] || echo "(Note: $TZ_NAME is not a time zone this server knows; check the spelling, e.g. Europe/Berlin.)"
env_set TIME_ZONE "$TZ_NAME"

ask ADDRESS "The address you will open it at (https://… behind your reverse proxy, or http://localhost:8000 to try it here)" "$(env_get PUBLIC_BASE_URL | grep . || echo http://localhost:8000)"
case "$ADDRESS" in http://*|https://*) ;; *) fail "The address starts with http:// or https://" ;; esac
env_set PUBLIC_BASE_URL "${ADDRESS%/}"
ask COMPANY "Your company's name" "$(env_get APP_NAME | grep -v '^Control Tower$' || echo 'Control Tower')"
env_set APP_NAME "$COMPANY"

say "The database's TLS certificate"
deploy/postgres-tls.sh

say "Building and starting (the first time takes a few minutes)"
# `up` fails when the server never becomes healthy; the check below shows why.
docker compose up -d --build || true

say "Waiting until it is healthy"
for _ in $(seq 1 90); do
  state="$(docker compose ps app --format '{{.Health}}' 2>/dev/null || true)"
  [ "$state" = "healthy" ] && break
  sleep 2
done
[ "$state" = "healthy" ] || { docker compose logs --tail 40 app; fail "The server did not become healthy within 3 minutes; its last log lines are above."; }
echo "Running."

say "The first owner"
if docker compose exec -T app /app/backend/.venv/bin/python -m app.admin_cli has-owner; then
  echo "An owner exists already. To invite another admin, use Admins in the admin."
else
  ask EMAIL "Your e-mail address (you sign in with it)" ""
  ask NAME "Your name" ""
  [ -n "$EMAIL" ] && [ -n "$NAME" ] || fail "An e-mail address and a name are needed: ./install.sh --email you@example.com --name \"Your Name\""
  docker compose exec -T app /app/backend/.venv/bin/python -m app.admin_cli create-owner --email "$EMAIL" --name "$NAME"
fi

say "Done"
cat <<EOF
Next:
  1. Open the link above, set your password, then add the authenticator app (every sign-in needs both).
  2. Follow "Getting started" in the admin: your company, the AI your team thinks with, the team's budget.
  3. Backups: add this line with 'crontab -e' for a backup every night at 3:00 (kept 14 days):
       0 3 * * * cd $(pwd) && ./scripts/backup.sh >> backups/backup.log 2>&1
  4. Updates later: ./scripts/update.sh (it backs up first and goes back if the new version does not start).
EOF
case "$ADDRESS" in
  https://*) echo "  5. Your reverse proxy (Caddy, nginx) must send $ADDRESS to 127.0.0.1:$(env_get APP_PORT | grep . || echo 8000) — see README.md." ;;
esac
