#!/usr/bin/env bash
# One-command launch for a Linux VPS with Docker Engine and the Compose plugin.
# The first run creates .env, generates a MemoryDB admin token, builds the image,
# starts MemoryDB and waits until the app answers its health endpoint.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Engine first: https://docs.docker.com/engine/install/" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin is required (docker compose, not docker-compose)." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Cannot talk to the Docker daemon. Start it, or run this with the right permissions." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi

if grep -Eq '^MEMORYDB_TOKEN=(replace-with-a-long-random-token)?$' .env; then
  token=$(openssl rand -hex 32 2>/dev/null \
    || python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null \
    || od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  sed -i "s/^MEMORYDB_TOKEN=.*/MEMORYDB_TOKEN=${token}/" .env
  chmod 600 .env
  echo "Generated a random MemoryDB token in .env."
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a
: "${MEMORYDB_TOKEN:?MEMORYDB_TOKEN is empty in .env}"
if [ "${#MEMORYDB_TOKEN}" -lt 16 ]; then
  echo "MEMORYDB_TOKEN must be at least 16 characters. Generate one with: openssl rand -hex 32" >&2
  exit 1
fi

echo "Pulling the MemoryDB image..."
docker compose pull memorydb

echo "Building and starting the stack..."
docker compose up -d --build

port="${UNIVORYA_PORT:-8000}"
echo "Waiting for Univorya on http://127.0.0.1:${port} ..."
for _ in $(seq 1 60); do
  # Ask from inside the app container, so the check needs no host curl/wget.
  if docker compose exec -T univorya python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" >/dev/null 2>&1; then
    echo
    echo "Univorya is ready: http://127.0.0.1:${port}"
    echo "MemoryDB data and uploads persist in Docker volumes."
    echo "Next: bash scripts/backup.sh   (online backup of the database and uploads)"
    exit 0
  fi
  sleep 2
done

echo "Univorya did not become healthy in time. Recent logs:" >&2
docker compose logs --tail=100 univorya >&2 || true
docker compose ps >&2 || true
exit 1
