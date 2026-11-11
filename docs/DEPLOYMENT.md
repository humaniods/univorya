# Deploying Univorya with MemoryDB

This is the production path: **one Univorya container** and **one MemoryDB container** on a Linux VPS,
managed by Docker Compose. MemoryDB stores users, sessions, projects, embedded media metadata and likes;
uploaded image bytes remain on the Univorya volume.

```
Internet ──► Caddy (443, optional) ──► Univorya :8000 ──► MemoryDB :8080 (private)
                                             └──────────► /data/uploads (volume)
```

SQLite is still available as a local development fallback, but it is not used by this stack.

## Prerequisites

- A Linux VPS (2 GB RAM is a reasonable starting point) with Docker Engine 24+ and the Compose plugin.
- A DNS name if you want automatic HTTPS.
- Outbound access to `ghcr.io` for the MemoryDB image.

Windows has no native MemoryDB build. Use Docker Desktop/WSL or a Linux host.

## Quick start

```bash
git clone https://github.com/humaniods/univorya.git
cd univorya
bash scripts/launch.sh
```

`launch.sh` copies `.env.example` to `.env` on first run, generates a 64-character MemoryDB token,
builds the app, starts MemoryDB, and waits for `http://127.0.0.1:8000/api/health`.

Or do it by hand:

```bash
cp .env.example .env
# edit .env and set MEMORYDB_TOKEN to a long random value (openssl rand -hex 32)
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/api/health
```

The health answer includes `"store": "memorydb"` when the MemoryDB path is active.

## Configuration

`.env` is read by Docker Compose:

| Variable | Default | Purpose |
|---|---|---|
| `MEMORYDB_TOKEN` | required | Admin bearer token shared by MemoryDB and the app. Use 16+ random characters, ideally 64 hex. |
| `MEMORYDB_IMAGE` | `ghcr.io/humaniods/memorydb:latest` | Pin a release tag for reproducible upgrades. |
| `MEMORYDB_PORT` | `8080` | Loopback port for operator access to MemoryDB. Never expose publicly. |
| `UNIVORYA_PORT` | `8000` | Published app port. |
| `UNIVORYA_BIND` | `0.0.0.0` | Set to `127.0.0.1` when Caddy is the public entry point. |
| `UNIVORYA_SEED_DEMO` | `1` | `0` disables the eight fictional demo projects. |
| `UNIVORYA_DOMAIN` | `example.com` | Domain for the optional Caddy HTTPS profile. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Uvicorn proxy trust. Set `*` **only** when the app port is loopback-bound behind Caddy. |
| `GOOGLE_CLIENT_ID` | empty | OAuth 2.0 Web client id. Empty hides Google sign-in. |
| `GOOGLE_CLIENT_SECRET` | empty | OAuth client secret; keep it only in `.env`. |
| `GOOGLE_REDIRECT_URI` | derived | Must equal the URI registered in Google Cloud Console, e.g. `https://showcase.example.com/auth/google/callback`. |
| `UNIVORYA_STUDIO` | `1` | `0` hides Studio and every Studio route/entry point. |
| `UNIVORYA_SECRET_KEY` | generated file | Stable random value (`openssl rand -hex 32`) that encrypts stored model keys and Git tokens. Changing it makes existing secrets unreadable. |
| `UNIVORYA_GIT_BINARY` | `git` on PATH | Explicit Git executable when the service account's PATH does not include it. |
| `UNIVORYA_STUDIO_CLI_TIMEOUT` | `300` | Seconds before one local agent CLI run is stopped. |
| `UNIVORYA_PREVIEW_ORIGIN` | empty | Dedicated HTTPS preview hostname; empty uses direct loopback ports. |
| `UNIVORYA_APP_ORIGIN` | empty | Public app origin, e.g. `https://showcase.example.com`. Required when the preview host differs, so the signed preview can allow that one framing ancestor instead of `SAMEORIGIN`. |

The app itself reads `UNIVORYA_STORE`, `MEMORYDB_URL`, `MEMORYDB_TOKEN`, `UNIVORYA_DATA_DIR`,
`UNIVORYA_SEED_DEMO` and the Studio variables above. Compose sets them for you.

### Google Sign-In on a VPS

1. In Google Cloud Console create an **OAuth consent screen** (External is fine; add test users while the app
   is unverified) and an **OAuth 2.0 Client ID** of type *Web application*.
2. Add `https://showcase.example.com/auth/google/callback` under **Authorized redirect URIs**.
3. Put the client id and secret in `.env`, set `GOOGLE_REDIRECT_URI` to that exact URL, and recreate the app:

   ```bash
   docker compose up -d --force-recreate univorya
   curl -s https://showcase.example.com/api/metadata | grep -o '"google":{[^}]*}'
   ```

   The metadata answer must say `{"enabled":true}`; the consent screen then appears on `/login`.

Google only accepts `http://localhost` for plain-HTTP local redirects (not `127.0.0.1`) and requires HTTPS for
a public domain. If the scheme/host the app receives differs from the registered URI, the callback is rejected by
Google with `redirect_uri_mismatch` before it ever reaches Univorya - check `FORWARDED_ALLOW_IPS` and
`GOOGLE_REDIRECT_URI` first.

### Using an external MemoryDB

To point at a MemoryDB instance outside this Compose project, remove the `memorydb` service and set:

```dotenv
MEMORYDB_URL=memorydbs://TOKEN@db.example.com:443
```

or `https://db.example.com` plus `MEMORYDB_TOKEN`. Use TLS (`memorydbs://`/`https://`) for any
traffic that leaves a private network.

## Putting it on the internet

The default stack publishes the app on port 8000 with no TLS, which is fine for a private pilot.
For a public site, use the bundled Caddy profile:

```dotenv
UNIVORYA_DOMAIN=showcase.example.com
UNIVORYA_BIND=127.0.0.1
FORWARDED_ALLOW_IPS=*
```

```bash
docker compose --profile https up -d --build
```

Point DNS at the VPS and allow inbound TCP 80 and 443 (HTTP-01/TLS-ALPN certificate issuance).
Caddy obtains and renews the certificate automatically and adds HSTS. Then close 8000 and 8080
from the public firewall:

```bash
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Why `FORWARDED_ALLOW_IPS=*`? Uvicorn only trusts `X-Forwarded-Proto` from addresses in this list.
Caddy lives at a dynamic container address, so the app would otherwise see plain HTTP, and its
same-origin check would reject HTTPS form writes. Trusting all proxies is safe only because
`UNIVORYA_BIND=127.0.0.1` keeps the app port off the public interface; do not combine `*` with
a publicly bound app port unless another trusted proxy enforces the headers.

If you terminate TLS in Caddy on the same host but want a narrower allowlist, set
`FORWARDED_ALLOW_IPS` to the Compose network CIDR instead of `*`.

## Data and persistence

| Volume | Contents |
|---|---|
| `univorya_memorydb-data` | `memory.wal`, the MemoryDB journal (users, sessions, projects, media metadata, likes), and online `.bak` snapshots. |
| `univorya_univorya-data` | Uploaded WebP files under `uploads/`, plus the unused SQLite fallback. |
| `univorya_caddy-data`, `univorya_caddy-config` | TLS certificates and Caddy state. |

Volume names assume the Compose project is called `univorya` (the directory name). Confirm with
`docker volume ls`.

Losing `memorydb-data` loses accounts, projects, likes and media metadata. Losing `univorya-data`
loses the image files those metadata rows point at. Back up both.

## Backups

```bash
bash scripts/backup.sh
```

The script asks MemoryDB for an online, checksummed snapshot while it keeps serving, copies the
`.bak` and its `.sha256` sidecar into `./backups`, then archives `uploads/` from the app container.
It keeps the newest 14 of each locally and prunes older snapshots inside `memorydb-data` too
(MemoryDB itself never overwrites or deletes a backup). Copy `./backups` to off-machine storage
(and encrypt it — the journal contains password hashes and session records).

Schedule it from cron (adjust the checkout path) and review the log:

```cron
17 3 * * * cd /opt/univorya && mkdir -p backups && flock -n backups/.backup.lock bash scripts/backup.sh >> "$HOME/univorya-backup.log" 2>&1
```

Run the job once by hand after installation, and again after a restore drill, before trusting it.

Manual equivalent:

```bash
docker compose exec -T memorydb sh -c \
  'wget -qO- --header="Authorization: Bearer $MEMORYDB_TOKEN" \
   --post-data="" http://127.0.0.1:8080/api/v1/admin/backup/'
```

The JSON response names the file and its SHA-256. Backups are never overwritten.

### Restore

Stop the app so nothing writes while you restore. MemoryDB restores into a **new** data file, then
you verify it and swap it in:

```bash
docker compose stop univorya memorydb

# The backup and its .sha256 sidecar must sit together in ./backups.
docker compose run --rm --no-deps -v "$PWD/backups:/restore:ro" --entrypoint sh memorydb -c '
  memorydb -data /data/restored.wal -restore /restore/REPLACE_WITH_BACKUP.bak &&
  memorydb -data /data/restored.wal -verify &&
  mv /data/memory.wal /data/memory.wal.pre-restore &&
  mv /data/restored.wal /data/memory.wal'

# Uploads, from the matching archive:
docker compose run --rm --no-deps -v "$PWD/backups:/restore:ro" --entrypoint sh univorya -c '
  rm -rf /data/uploads &&
  tar -xzf /restore/REPLACE_WITH_UPLOADS.tar.gz -C /data'

docker compose up -d
```

Keep the old `memory.wal` until you have checked the restored site. To roll back, stop the stack and
move `memory.wal.pre-restore` back. Test a restore on a non-production host before relying on it.

## Upgrades

```bash
bash scripts/backup.sh
docker compose pull
docker compose up -d --build
```

MemoryDB's journal format is versioned: an older journal opens under a newer binary, but not the
reverse. Back up before every MemoryDB upgrade, and pin `MEMORYDB_IMAGE` to a tested tag. The app
image is built from this repository.

## Migrating an existing SQLite deployment

The migration copies users, projects, embedded media metadata and likes. Sessions are **not**
migrated, so everyone signs in again. Run it with Univorya stopped and after a backup:

```bash
docker compose stop univorya
docker compose run --rm univorya python scripts/migrate_sqlite_to_memorydb.py \
  --sqlite /data/univorya.sqlite3 \
  --memorydb http://memorydb:8080
docker compose up -d univorya
```

`MEMORYDB_TOKEN` comes from the service environment. The script matches existing users by username,
projects by slug and likes by `(user, project)`, so re-running it is safe. It reports created,
already-present and skipped rows. For a local Python run:

```bash
python scripts/migrate_sqlite_to_memorydb.py --sqlite data/univorya.sqlite3 \
  --memorydb memorydb://TOKEN@127.0.0.1:8080
```

Keep the SQLite file and the old uploads directory until the MemoryDB deployment has been verified.
Media metadata references the same `/uploads/<file>` names, so no image files need rewriting.

## Operating notes and security checklist

- **One app process.** `MEMORYDB_URL` mode is designed for a single Uvicorn worker: like toggles are
  serialised in-process to avoid create/delete races, and uploads live on one local volume. Do not
  add `--workers N` or run multiple app replicas against the same volumes without redesigning both.
- **The token is an admin credential.** Anyone with `MEMORYDB_TOKEN` can read and write every
  document. Treat `.env` as a secret (`chmod 600`), keep it out of Git, and rotate it by changing
  both services together. `GOOGLE_CLIENT_SECRET` is a secret for the same reason.
- **Google OAuth is optional and off by default.** With no `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
  the button is hidden and `/auth/google/start` answers 503. The callback enforces `state`, PKCE and a
  Google-verified email; keep `GOOGLE_REDIRECT_URI` identical to the Console entry.
- **MemoryDB is single-node.** It is not a clustered database. Its WAL is the source of truth, so
  keep the volume on local disk, not a network filesystem.
- **No database encryption at rest.** Use full-disk encryption (LUKS) on the VPS if the journal
  contains data you would not want on a stolen disk. The exception is Studio model keys and Git
  tokens, which are Fernet-encrypted before they reach `studio.sqlite3`; that protects a copied
  database file, not a compromised running process or host.
- **Studio's local runner is not a sandbox.** It executes agent commands and locally installed
  agent CLIs as the app account inside `/data/studio`, with timeouts and a workspace boundary, but no
  container or seccomp. Agent CLIs inherit the service account's home directory so an existing
  `claude`/`codex` login keeps working — that also means they can read anything that account can read.
  Keep `UNIVORYA_STUDIO` off on multi-tenant installs, or build and enforce a per-workspace container
  runner before opening signups. Set a stable `UNIVORYA_SECRET_KEY`; without it the app
  generates `studio/secret.key` in the data volume.
- **Git tokens are per user and never returned.** The API exposes only a username and last-four hint.
  Clone/push pass the token to Git through an environment-backed `http.extraHeader`, not argv, with
  terminal prompts disabled. SSH URLs are rejected; use an HTTPS personal access token. The app never
  hosts repositories — Studio only clones, commits and pushes to the remote the owner names.
- **TLS only for public traffic.** Put Caddy in front, or supply a TLS terminator you already trust.
- **Uploads are re-encoded.** Only raster PNG/JPEG/WebP/GIF images are accepted, size and pixel
  limits are enforced, and files are served with `X-Content-Type-Options: nosniff`. This is not an
  antivirus scanner.
- **Abuse controls are per process.** Login/signup caps and like serialisation live in the app.
  Add edge rate limiting, email verification and a takedown workflow before an open public launch.
- **Backups are not optional.** Schedule `scripts/backup.sh` (cron/systemd timer), copy snapshots
  off the host, and monitor for failures.
- **Health checks.** `GET /api/health` returns `503` with `"status": "degraded"` when MemoryDB is
  unreachable; the container healthcheck uses the same route.

## Troubleshooting

| Symptom | Check |
|---|---|
| `MemoryDB is not ready after 30 attempts` | `docker compose logs memorydb`; token length; the `.env` value matches in both services. |
| App restarts with `MEMORYDB_TOKEN is required` | `.env` is missing or the token line is empty. |
| Every form write returns 403 behind Caddy | `FORWARDED_ALLOW_IPS` does not trust Caddy; see the TLS section. |
| `401` from MemoryDB | Token mismatch between `memorydb` and `univorya`. |
| Uploads 404 after a restore | The uploads archive was not restored into `/data/uploads`. |
| `docker compose` warns about `version` | This stack does not use the obsolete top-level `version` key. |
| Health says `store: sqlite` in production | `UNIVORYA_STORE`/`MEMORYDB_URL` were overridden outside Compose. |
