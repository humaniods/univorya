# Univorya

**Built by anyone. Discovered by everyone.**

A real, responsive showcase for apps, open-source projects, AI tools, designs, prototypes, experiments and
ideas — with accounts, uploads, discovery and an owner's own control over what they published. It is not a
clones-only website, and it does not host the things it points at.

![Univorya home: the landing grid of project cards, seen at 1440px](docs/screens/home.png)

**Scope:** Univorya stores project descriptions and images. It never runs code uploaded by visitors, hosts Git
repos, serves model weights or distributes APK/IPA binaries. **Studio** is the one exception to "no code": a project's
owner can scaffold or clone a private workspace and let an AI agent (or their own installed Claude Code / Codex CLI)
write files into it, which the owner previews beside the chat. The brand is **provisional** until domain and trademark clearance.

**Production store:** Univorya runs on [MemoryDB](https://github.com/humaniods/MemoryDB) — users, sessions,
projects, embedded media metadata and likes are MemoryDB documents, while image bytes stay on a local volume.
SQLite remains the zero-setup development fallback. The included Compose stack starts both services, and
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) covers TLS, backups, restore and migration.

## What it looks like

| Publish | Your own uploads |
|---|---|
| The form every category goes through, with the cover preview showing the file's real size and weight. | A freshly picked screenshot tile, animating in with the pixel size and weight read from the file before it is uploaded. |
| ![The publish form with a cover image picked](docs/screens/submit.png) | ![A picked screenshot tile in the media list](docs/upload-tile.png) |

| Discovery | Lightbox | Portfolio | Honest 404 |
|---|---|---|---|
| Search, category and stage filters, sort and load-more. | Keyboard-navigable gallery with the file's own facts in the caption. | Creator page with project, view and appreciation counters. | Unknown routes and missing assets answer truthfully. |
| ![Explore grid filtered by category](docs/explore-pagination.png) | ![Screenshot enlarged in the lightbox](docs/lightbox.png) | ![Maker profile for @morganbuilds](docs/screens/maker.png) | ![Not found page](docs/not-found.png) |

| Mobile | After pruning the gallery |
|---|---|
| The same app at 390px, no horizontal scroll, drawer navigation. | Removing every screenshot leaves the cover standing — the case that used to fail silently. |
| ![Univorya home on a 390px viewport](docs/home-mobile.png) | ![Project detail with an emptied screenshot gallery](docs/gallery-cleared.png) |

Every image above is produced by `python tests/browser_flow.py` against a throwaway instance, so the README
cannot drift from what the test actually sees.

## Use cases

Each flow is the path a person takes, what the server keeps, and the check that keeps it working.

### UC-01 · Publish an idea with nothing to deploy
- **Who:** student or newcomer with a concept — no repo, no live site.
- **Path:** `/join` → `/submit` → category *Idea*, stage *Idea*, optional cover → publish.
- **Stored:** one `projects` row plus one `project_media` row per image; the repo/demo/website columns stay empty strings, which is a valid submission.
- **On screen:** a stable `/p/<slug>` page credited to them, and the card animating into `/dashboard` and their maker profile.
- **Proven by:** the Chromium flow publishing an *Idea* entry through the real form, then reading the row back out of SQLite.

### UC-02 · Show an app with its screens
- **Who:** indie developer launching a product.
- **Path:** `/submit` → cover + up to 8 screenshots → stack, tags, stage, links → publish → `/edit/<slug>` later to drop shots or swap the cover.
- **Stored:** each file is decoded, stripped of camera/GPS metadata, shrunk (never upscaled), re-encoded to WebP, and recorded as a `project_media` row with width, height, byte size, animation flag and timestamp.
- **On screen:** the gallery tiles and lightbox caption render those stored facts; the creator's own edit form adds the upload date, so no returned field is fetched and discarded.
- **Proven by:** `test_each_stored_upload_reports_the_row_behind_it`, the metadata-stripping test, and the browser flow's tile assertions.

### UC-03 · Credit an open-source project properly
- **Who:** maintainer of a fork or an independently built project.
- **Path:** `/submit` → category *Open Source* → source repository URL + license field → contributors named in the description.
- **Stored:** the row keeps `repo_url` and `license`; links are scheme-checked and open with `rel="noopener noreferrer"`. Univorya never mirrors the repository itself.
- **On screen:** link buttons on the detail page, license in the project snapshot, attribution in the article body.
- **Proven by:** the URL-scheme and Origin checks in the API suite plus the category sweep that publishes an *Open Source* entry.

### UC-04 · Showcase an AI or design tool without shipping weights
- **Who:** AI engineer, designer or prototyper.
- **Path:** `/submit` → *AI & ML* / *Design* → demo or website link → screenshots, including an animated GIF/WebP preview.
- **Stored:** animations keep their frames and loop timing inside a hard budget — ≤5 MB per file, ≤40 frames, ≤40M decoded pixels per upload, ≤8 files and ≤25 MB combined.
- **On screen:** an `animated` badge on the tile, and the stored WebP served with immutable caching.
- **Proven by:** the animation-frame and pixel-budget tests, and the flow's `live`/`animated` flag checks.

### UC-05 · Browse, appreciate, and find the person behind the work
- **Who:** visitor, hiring manager, or another maker looking for prior art.
- **Path:** `/explore` → keyword search, category and stage filters, sort, load-more → open a project → like it → click through to `/makers/<username>`.
- **Stored:** one like per user per project, view counters, and the profile's project/upload/appreciation counts computed from the same rows.
- **On screen:** staggered card reveals, count-ups, and a profile that reports real numbers rather than decoration.
- **Proven by:** the discovery API tests and the flow's search, like and profile-counter steps.

### UC-06 · Stay in control of what you published
- **Who:** any signed-in creator.
- **Path:** `/dashboard` → edit (slug preserved) → drop or restore individual screenshots → delete the project.
- **Stored:** writes are owner-gated; deleting removes the project row, its `project_media` rows and every file it owned, leaving no orphan on disk.
- **On screen:** the edit form lists stored images with a remove/restore control per screenshot; signed-out visitors never see a form that cannot save.
- **Proven by:** the owner-only, orphan-file, `keep_media` presence and gallery-clearing tests, plus the signed-out gate step in the browser flow.

### UC-07 · Run or extend it locally
- **Who:** anyone reading the code.
- **Path:** `python scripts/preview.py` (starts the server and opens your browser), `docker compose up --build`,
  or Uvicorn with `UNIVORYA_DATA_DIR` pointing at a directory you own.
- **Stored:** MemoryDB in production (`univorya_users`, `univorya_sessions`, `univorya_projects`,
  `univorya_likes`; media metadata embedded) or SQLite for local development, plus `uploads/`.
- **On screen:** the app at `127.0.0.1`, with `/api/docs` and `/api/health`.
- **Proven by:** the HTTP smoke script run against a real server, and the test suite's throwaway instances.

### UC-08 · Open the thing you are showing, while it still only runs on your laptop
- **Who:** the creator of the project, on the machine where that project is running.
- **Path:** `/submit` → **Local preview address** → project page → **ON THIS MACHINE** panel → `Open on this
  machine`; or `python scripts/showcase.py <slug>` for a Chrome window with no tabs and no address bar.
- **Stored:** `projects.preview_url` — one column, `http(s)` on `127.0.0.1`, `localhost` or `::1` only, anything
  else refused with a 422 before the row is written.
- **On screen:** the address, the button and the terminal command appear in the aside **for the owner only**; a
  visitor's payload has no `preview_url` key at all, so there is nothing to render.
- **Proven by:** the 29 checks in `tests/test_local_preview.py`, and the browser flow, which fills the field
  through the real form, watches the button open a second window on that exact address, then loads the same page
  signed out and asserts the address is missing from both the DOM and the API response.

### UC-09 · Build the project beside a live preview, with your own model key or agent CLI
- **Who:** the project's owner, on `/studio/<slug>`, signed in.
- **Path:** **Open Studio** on the project page or the **Studio** chip on a dashboard card → pick a starter
  (HTML/CSS/JS, Python/FastAPI, Node, clone a Git repository or empty) → save an OpenAI, Anthropic, OpenRouter,
  custom OpenAI-compatible key, or select a local **Claude Code / Codex / Gemini / custom CLI** → describe the
  change → **Start preview**.
- **Stored:** a separate `DATA_DIR/studio/studio.sqlite3` sidecar holds the workspace, its port/PID, the chat
  transcript and tool events. Model keys and Git tokens are encrypted with Fernet (`UNIVORYA_SECRET_KEY`, or a
  generated `studio/secret.key` locally) and are never returned by the API — only a provider/model or username and
  last-four hint.
- **On screen:** a split workspace — conversation and tool chips on the left, the running app in an iframe on the
  right — plus file, Git and model tabs, a preview log, and **Open** buttons that pop the live app into a normal
  browser window. The agent can read/write files, run commands, clone, diff, commit, pull and push.
- **Proven by:** `tests/test_studio.py` — owner-only access, scaffolding, encrypted-at-rest keys and Git tokens,
  the key gate on chat, a scripted agent loop writing a real file, a local bare-repo round trip
  (clone → commit → push → pull) through both the API and the agent tools, a fake agent CLI editing the workspace,
  and a live server proxied through a signed `/preview/<token>/` URL with `SAMEORIGIN` framing and the page CSP
  removed only for that route.

## Limitations we ran into — and closed

| The limitation | What it is now | Where the proof lives |
|---|---|---|
| One cover image per project, and a file on disk with no record of itself | Cover **plus** up to 8 screenshots, every file a `project_media` row with dimensions, weight, animation flag and timestamp | API media tests; browser category sweep |
| Uploads kept their EXIF: camera, software, capture time, GPS | Re-encode strips metadata and shrinks without upscaling | `test_a_re_encoded_image_loses_the_camera_and_location_metadata` |
| An animated upload could ask the server for far more pixels than a static one | Per-upload decode budget, frame cap and total byte cap; rejected uploads leave no file behind | animation budget tests |
| Client-side validation only, and an empty `keep_media` list was indistinguishable from an omitted one | Server validates on both verbs; field presence is read from the parsed form | `keep_media` tests |
| No limit on repeated password guesses | Sliding-window throttle: 10 per account, 30 per client, 15 signups per client per 10 minutes (in-process) | throttling tests |
| A canonicalisation redirect that could hand a visitor `//attacker.example` | Redirect targets are built same-origin, and the test forbids any `Location` starting with `//` or a scheme | `test_a_double_slash_cannot_make_the_canonical_redirect_leave_our_origin` |
| Google Fonts `@import` and inline `onclick` handlers, both silently blocked by our own CSP | Self-hosted variable font subsets; one delegated `data-action` handler | hardening source guards |
| Unknown routes answered with the explore grid; a typo'd asset URL returned the HTML shell | Real not-found view; extension-bearing paths 404 | not-found and missing-asset tests |
| A malformed `%zz` in a URL threw and left a blank page | Defensive decode with a raw-segment fallback | malformed-path step in the browser flow |
| Scroll-reveal CSS could leave content permanently invisible if a re-render skipped the observer | Observer is re-armed after every render; `prefers-reduced-motion` shows everything at full opacity with zero running animations | the flow fails if any `.reveal` stays at opacity 0 |
| HTML cached as if it were static, API responses cacheable | Policy by volatility: `no-store` for `/api`, `immutable` for content-addressed media and fonts, `no-cache` elsewhere | `test_cache_policy_matches_how_often_a_url_can_change` |
| Sample content could be mistaken for real users | Fictional demo cards are labelled in the UI and switch off with `UNIVORYA_SEED_DEMO=0` | demo-labelling tests |
| A project that only runs on its author's laptop had nowhere to point: every link field assumed it was deployed | A loopback-only `preview_url`, validated on both verbs, hidden from every non-owner payload, and opened by an owner-only button or `scripts/showcase.py` | `tests/test_local_preview.py`, the popup and signed-out steps in the browser flow |

## Limitations we have not closed

Honest list, not a to-do we pretend is done:

- No live deployment: no public URL, production TLS, managed storage or Postgres, and no CDN in front of the
  uploads.
- Password signups have no email verification, and there is no password reset, captcha, abuse reporting, moderation
  or takedown workflow — do not open signup to strangers before those exist. Google sign-in, when configured, only
  accepts an address Google marks as verified.
- The throttle map is in-memory and per process: multiple workers each hold their own counters, a restart clears
  them, and behind a reverse proxy every visitor can collapse into one bucket unless `--forwarded-allow-ips` and
  `X-Forwarded-For` rewriting are configured. See [docs/TEST_REPORT.md](docs/TEST_REPORT.md).
- Single Chromium engine, one viewport pair, no full WCAG audit, load test or penetration test.
- Local preview is the creator's own machine only. A page served over HTTP cannot start a program on the visitor's
  computer, so `Open on this machine` opens a window in the browser the creator is already using, and the tab-less
  Chrome window needs the local `scripts/showcase.py`. Visitors get the public links, never the loopback address.
- Studio's local runner executes the agent's commands as the app service account, inside `DATA_DIR/studio`, with
  timeouts — not inside a container. That is acceptable for a single-user self-hosted install, not for public
  multi-tenant use. A per-workspace Docker runner and a dedicated preview origin are the production follow-ups.
- Studio previews work best as static or simple server apps. The signed proxy rewrites HTML attributes and CSS
  `url()` values, but an app that calls root-absolute URLs from inside JavaScript needs the direct loopback origin
  (the default in local mode) or a dedicated preview domain.
- The vendored OFL font subsets still need an attribution file, and there are no privacy/terms pages yet.
- Phase 2 items — GitHub identity verification, teams, unlisted drafts, analytics, notifications — are
  unimplemented. See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Features

- Dark glassmorphism UI with responsive desktop/mobile layouts and a custom SVG wordmark.
- Creator accounts: register, login, logout, scrypt-hashed passwords, HttpOnly session cookies.
- **Google Sign-In** with the authorization-code + PKCE flow, verified-email account linking and a password-only
  fallback; the button stays hidden until OAuth credentials are configured.
- **MemoryDB in production** — four collections with unique indexes, real record-level session expiry, embedded
  media metadata and a denormalised like counter; SQLite stays for local development.
- Publish **projects and ideas** with title, description, tags, 8 categories, 4 lifecycle stages, technologies,
  license and optional demo/GitHub/website links.
- Public project detail pages, creator portfolios, discovery filters/search, sort and load-more pagination.
- Motion layer of 20 keyframe animations — scroll reveals, staggered cards, count-ups, toasts, drawer and
  lightbox transitions — with a full `prefers-reduced-motion` fallback.
- Logged-in likes, owner-controlled edit/update and delete, saved image replacement.
- Owner-only **local preview**: store the loopback address your project runs on and open it straight from your own
  showcase page, or in a tab-less Chrome window with `scripts/showcase.py`.
- **Studio**, an AI coding workspace inside a project: the owner brings their own OpenAI/Claude/OpenRouter key or
  selects a locally installed agent CLI (Claude Code, Codex, Gemini, custom), chats with an agent that can read/write
  files, run commands and manage Git, and watches the running app beside the chat in a split pane. Keys and Git
  tokens are Fernet-encrypted per user; workspaces are private and owner-gated.
- 142 API tests (including MemoryDB integration over a fake HTTP server, the Google OAuth handshake, Studio Git
  round trips and agent CLI runs) and a Chromium interaction smoke that boots a real server, both green on GitHub
  Actions for every push to `main`, plus a real-network smoke script, Docker, and reference screenshots generated by
  the tests themselves.

## Start with Docker (production)

```bash
bash scripts/launch.sh        # creates .env and a random token on first run
```

Open http://localhost:8000. This starts **MemoryDB + Univorya**: database documents live in `memorydb-data`,
uploaded images live in `univorya-data`, and online snapshots are written into `memorydb-data` then copied
out by `scripts/backup.sh`.
`scripts/launch.sh` generates a token on first run and waits for `/api/health`.

For a real VPS, put Caddy in front for automatic HTTPS:

```bash
# set UNIVORYA_DOMAIN and UNIVORYA_BIND=127.0.0.1 in .env
docker compose --profile https up -d --build
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the security checklist, backup/restore, upgrades and migration.

### SQLite-only development stack

```bash
docker compose -f docker-compose.sqlite.yml up --build
```

Open http://localhost:8000. SQLite and uploads persist in the named volume `univorya-data`.

## Start with Python (3.11+)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000. Swagger API docs at `/api/docs` and health endpoint `/api/health`.

SQLite is used by default. `UNIVORYA_DATA_DIR=./data` chooses where the database and uploaded images are stored.
To use MemoryDB locally, start it (Docker is the supported way on Windows) and export:

```bash
export MEMORYDB_URL=memorydb://TOKEN@127.0.0.1:8080
# or: export MEMORYDB_URL=http://127.0.0.1:8080 MEMORYDB_TOKEN=TOKEN
export UNIVORYA_STORE=memorydb        # optional when MEMORYDB_URL is set
```

`UNIVORYA_STORE=sqlite` forces the local fallback even when `MEMORYDB_URL` is present.

## Google Sign-In (optional)

Create an **OAuth 2.0 Web application** client in Google Cloud Console, add
`https://your-domain/auth/google/callback` (or `http://localhost:8000/auth/google/callback` for local work)
as an authorized redirect URI, then set three variables and restart:

```bash
export GOOGLE_CLIENT_ID=000000000000-xxxxxxxx.apps.googleusercontent.com
export GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx
export GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback   # must match the console exactly
```

The **Continue with Google** button appears on `/login` and `/join` only when both credentials are present;
otherwise the app is password-only and `/auth/google/start` answers 503. The flow uses the authorization-code
grant with **PKCE (S256)**, a signed-in-cookie `state` check and a 10-minute cookie lifetime, and only accepts
profiles whose email Google marks as verified. A first Google sign-in with an email that already has a password
account links the two (same user id); a brand-new identity gets a random unusable password hash, so the password
form can never sign into a Google-only account.

Offline development and demos use the same protocol against a local provider:

```bash
python -m uvicorn scripts.dev_mock_google:app --host 127.0.0.1 --port 8099
# then set GOOGLE_AUTH_URL/GOOGLE_TOKEN_URL/GOOGLE_USERINFO_URL to that port, plus:
#   demo account: aarav.google.demo@gmail.com / GoogleDemo-2026!
```

`scripts/dev_mock_google.py` is development-only and is not imported by the app or shipped in the production
image. Never point the three URL overrides at a non-Google host in production.

## Studio (bring your own model key)

Open a project you own and press **Open Studio**, or follow the **Studio** chip on its dashboard card. Pick a
starter, then save a key on the **Model** tab. Supported providers are OpenAI, Anthropic Claude, OpenRouter, any
OpenAI-compatible endpoint (set a base URL, including a local one), and a **local agent CLI**. The CLI option covers
Claude Code, Codex, Gemini and any command that accepts a `{prompt}` placeholder; the CLI must already be installed
and signed in on the server account, and Studio runs it inside the workspace. An optional key pasted in the same
tab is exported only to that child process.

The **Git** tab clones a repository into the workspace, shows branch/remote/status/ahead-behind, recent commits and
a diff, then commits and pushes with a per-user token. The same operations are agent tools, so the model can run
`git_status`, `git_diff`, `git_clone`, `git_commit`, `git_pull` and `git_push` when you ask. Clone accepts `https://`
URLs, loopback `http://` Git servers and absolute local paths; SSH URLs are rejected because Studio has no SSH agent.
Git tokens live in the same encrypted sidecar and the API returns only the username and last four characters.

```bash
# Encrypts stored model keys and Git tokens at rest. Set once and keep it stable in production.
export UNIVORYA_SECRET_KEY=$(openssl rand -hex 32)

# Optional: point at a git binary that is not on PATH.
# export UNIVORYA_GIT_BINARY=/usr/bin/git

# Optional: cap one agent CLI run (seconds, default 300).
# export UNIVORYA_STUDIO_CLI_TIMEOUT=300

# Optional: serve signed /preview/<token>/ pages from a dedicated hostname.
# If the preview host differs, also set the app's public origin:
# export UNIVORYA_PREVIEW_ORIGIN=https://preview.example.com
# export UNIVORYA_APP_ORIGIN=https://showcase.example.com

# Hide Studio entirely.
# export UNIVORYA_STUDIO=0
```

The key belongs to the signed-in user, is encrypted with Fernet before it reaches SQLite (or the configured data
directory), and the API only ever returns the provider, model and last four characters. Studio keeps its own
`DATA_DIR/studio/studio.sqlite3` sidecar, so enabling it does not change the main MemoryDB/SQLite schema. Workspaces
are owner-only; another signed-in user gets a 403.

Local mode starts the workspace on a loopback port and frames it directly (`http://127.0.0.1:<port>`); the **Open**
button at the top and in the preview bar pops the same URL into a normal browser window. Production should set
`UNIVORYA_PREVIEW_ORIGIN` (and `UNIVORYA_APP_ORIGIN` when the preview host differs) and put a container runner behind
it. The built-in runner is **not a sandbox**: it runs commands and agent CLIs as the app account, so it is suitable
for a single-user install, not for untrusted public signups. A per-workspace Docker runner is explicitly a follow-up,
not a feature of this build. The Docker image installs `git`; install Node-based agent CLIs in a derived image if you
want them inside the container. See `.env.example` for every Studio setting.

## See it in a real browser window

```bash
python scripts/preview.py            # starts the server if needed, then opens your browser
python scripts/preview.py --temp     # same, on a throwaway database — nothing touches data/
python scripts/preview.py --no-browser
```

The launcher polls `/api/health` before it opens anything, so the browser never lands on a half-booted server,
and if something is already serving the port it reuses that instance instead of failing to bind. It defaults to
`127.0.0.1:8123` with the `data/` directory; `--data-dir`, `--host` and `--port` move it.

To drive the window yourself instead — the way an agent or a reviewer would — open a dedicated app window, or
point an IDE's in-app browser at the same URL:

```bash
google-chrome --app=http://127.0.0.1:8123/ --user-data-dir=/tmp/univorya-view
```

The same trick is what **local preview** does for a creator's own project. Put `http://localhost:5173` (or any
`127.0.0.1` / `::1` address) in the **Local preview address** field on `/submit`, then from your project page:

```bash
python scripts/showcase.py <slug>            # opens it in a Chrome app window of its own
python scripts/showcase.py <slug> --print    # only print the stored address
```

The script reads the address out of SQLite in read-only mode, refuses anything that is not loopback, refuses to
open a window when nothing is listening on that port, and keeps its own profile directory so it never touches your
everyday Chrome. `--data-dir` and `--browser` move those choices.

The window is the full product: publish through `/submit` in any of the eight categories, then watch
`/dashboard`, your maker profile and the project page animate in. Every image you upload becomes a
`project_media` row, and its stored facts (kind, pixel size, weight, animation, upload date) are rendered from
that row on the gallery tile, in the lightbox caption and on your own edit form. `UNIVORYA_HEADED=1 python
tests/browser_flow.py` drives the same window end to end if you would rather watch the checks run.

## Run tests

```bash
pip install -r requirements-dev.txt
pytest --cov=app --cov-fail-under=85 -q   # 142 tests, including MemoryDB, Google OAuth, Studio, Git and CLI agents
node --check static/app.js                # JavaScript syntax
python tests/browser_flow.py              # Chromium UI smoke; regenerates the screenshots above
UNIVORYA_HEADED=1 python tests/browser_flow.py   # same run in a visible browser window
```

To run the real HTTP smoke separately, start Uvicorn then:

```bash
UNIVORYA_TEST_URL=http://127.0.0.1:8000 python scripts/smoke_http.py
```

The browser test boots a real Uvicorn server on a free port with a **temporary** data directory, so no test run
writes to `data/`. It publishes one project per category through the form and reads the rows back out of SQLite,
then fails if any reveal element never reaches full opacity. Coverage is a correctness signal, not a security
certification.

## Source layout

```text
app/main.py                    FastAPI backend: session auth, uploads, persistence, REST
app/studio.py                  Encrypted model keys/Git tokens, workspaces, providers, CLI agents, Git, preview proxy
app/memory_store.py            MemoryDB persistence adapter (stdlib-only HTTP client)
static/                        index.html, app.js, styles.css, logo, self-hosted fonts
scripts/smoke_http.py          Real-network regression check
scripts/preview.py             Start locally and open a real browser once /api/health answers
scripts/showcase.py            Open a creator's own loopback preview in a Chrome app window
scripts/launch.sh              One-command Linux VPS launch
scripts/backup.sh              Online MemoryDB backup + uploads archive
scripts/migrate_sqlite_to_memorydb.py  One-way SQLite → MemoryDB migration
tests/                         142 API checks + MemoryDB + Google OAuth + Studio/Git/CLI + Chromium smoke
.github/workflows/ci.yml       API + JS + Chromium pipeline
docs/                          Requirements, architecture, deployment, test report, screenshots
Dockerfile / docker-compose.yml / docker-compose.sqlite.yml / Caddyfile
```

## Documentation

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — personas, acceptance criteria, non-goals, release gate.
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — production launch, TLS, backups, restore, migration and security.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — data model, request flow, security notes and caveats.
- [docs/TEST_REPORT.md](docs/TEST_REPORT.md) — what was run, what was observed, and what is explicitly not claimed.
