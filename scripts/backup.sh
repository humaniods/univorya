#!/usr/bin/env bash
# Online backup: MemoryDB writes a consistent journal copy while it keeps serving,
# then this script copies that file and the uploaded images to ./backups.
#
#   bash scripts/backup.sh
#
# Restore instructions live in docs/DEPLOYMENT.md. Copy ./backups off the machine.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)

echo "Requesting an online MemoryDB backup..."
response=$(docker compose exec -T memorydb sh -c \
  'wget -qO- --header="Authorization: Bearer $MEMORYDB_TOKEN" --post-data="" http://127.0.0.1:8080/api/v1/admin/backup/')
path=$(printf '%s' "$response" | sed -n 's/.*"path":"\([^"]*\)".*/\1/p')

if [ -z "$path" ]; then
  echo "Backup request failed: ${response}" >&2
  exit 1
fi

base=$(basename "$path")
docker compose cp "memorydb:${path}" "backups/${base}"
docker compose cp "memorydb:${path}.sha256" "backups/${base}.sha256"
echo "MemoryDB: backups/${base} (+ .sha256)"

echo "Archiving uploaded images..."
docker compose exec -T univorya tar -czf - -C /data uploads > "backups/uploads-${stamp}.tar.gz"
echo "Uploads:  backups/uploads-${stamp}.tar.gz"

# MemoryDB never overwrites a snapshot, so prune the copies it wrote into the
# data volume too. The newest 14 survive, matching the host-side retention below.
docker compose exec -T memorydb sh -c \
  'ls -1t /data/memorydb-*.bak 2>/dev/null | tail -n +15 | while read -r old; do rm -f "$old" "$old.sha256"; done'

# Keep the newest 14 database snapshots and upload archives on this host.
ls -1t backups/memorydb-*.bak 2>/dev/null | tail -n +15 | while read -r old; do
  rm -f "$old" "$old.sha256"
done
ls -1t backups/uploads-*.tar.gz 2>/dev/null | tail -n +15 | while read -r old; do
  rm -f "$old"
done

echo "Done. Copy ./backups to off-machine storage and verify the .sha256 sidecars."
