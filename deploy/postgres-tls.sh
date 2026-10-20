#!/usr/bin/env bash
# Generate the TLS material the compose PostgreSQL serves and the app verifies.
#
# The app refuses to talk to PostgreSQL without TLS in production (backend/app/db.py),
# and `verify-full` means the certificate must name the host the app connects to —
# inside the compose network that is `db`. So: a private CA, and a server certificate
# for `db` signed by it. Both live in ./tls next to docker-compose.yml; the CA
# certificate is mounted into app and worker as DATABASE_SSL_ROOT_CERT.
#
# Re-run to rotate (10-year CA, 2-year leaf); `docker compose up -d` afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."
TLS=${1:-tls}
mkdir -p "$TLS"
cd "$TLS"

if [ -f ca.crt ] && [ -f server.crt ] && [ -f server.key ]; then
  echo "tls/ already has a CA and server certificate — delete them to rotate."
  exit 0
fi

openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout ca.key -out ca.crt -subj "/CN=Control Tower compose CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null

openssl req -newkey rsa:2048 -sha256 -nodes -keyout server.key -out server.csr \
  -subj "/CN=db" -addext "subjectAltName=DNS:db,DNS:localhost" 2>/dev/null

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 730 -sha256 \
  -extfile <(printf "subjectAltName=DNS:db,DNS:localhost\nextendedKeyUsage=serverAuth\n") 2>/dev/null

rm -f server.csr ca.srl
chmod 600 ca.key server.key
chmod 644 ca.crt server.crt
echo "wrote $(pwd)/{ca.crt,server.crt,server.key} — PostgreSQL serves TLS as 'db'."
