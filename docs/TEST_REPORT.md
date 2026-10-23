# Univorya — Local Test Results

**Run date:** 23 October 2026 — the API suite, HTTP smoke, the Chromium flow and every code fix below were
re-run against the working build on that date.

| Test suite | Command | Result |
|---|---|---|
| API and authorization | `pytest --cov=app -q` | **53 passed** |
| JS syntax | `node --check static/app.js` | **Passed** |
| Browser UI smoke | `python tests/browser_flow.py` | **Passed** |
| Real HTTP integration | `python scripts/smoke_http.py` against local Uvicorn | **Passed** |

## API cases covered

- Health and static assets; UI branding/security headers.
- Register, login, duplicate account, password validation, logout, HttpOnly cookies.
- **Brute-force caps:** 10 failed logins per account, 30 per client, 15 signups per client, each in a rolling
  10-minute window; the client bucket is what stops a leaked password list from being tried across accounts,
  and a blocked account stays blocked even when the next attempt carries the correct password.
- Failed logins never unlock signup, unknown accounts answer byte-identically to wrong passwords, and an
  expired or unknown session cookie reads as anonymous while every write answers 401.
- **Sign-in is asked before the body is parsed:** an anonymous POST/PUT with a junk body is a 401, not a 422
  that maps out the form schema.
- Actual PNG upload, image conversion to WebP, public file serving.
- **Every category (Web App, Mobile App, AI & ML, Dev Tool, Open Source, Design, Idea, Other) persists its project row plus one `project_media` row per uploaded file, in `[cover, screenshot, screenshot]` order.**
- **Each media row answers with its own `uploaded_at`** (the row's UTC timestamp, ISO-parseable and never in the
  future), which is the data behind the date on the creator's own upload tiles.
- Cover plus four screenshots round-trips through list, detail, download, a partial prune, a text-only edit
  (which must not touch the gallery) and an explicit clear-all.
- `keep_media` distinguishes *omitted* from *sent empty*: an empty list really does remove every screenshot,
  and only screenshots belonging to that project can be removed by it.
- A picked file that arrives with zero bytes is rejected instead of being dropped in silence.
- **A phone-style JPEG export loses its owner and location on the way in:** camera model, device/software and
  capture-time tags are absent from the stored WebP bytes, and the image is shrunk but never upscaled.
- Metadata advertises the same limits the client validates against (per-file MB, 25 MB combined, 8 screenshots, 200×150 minimum).
- Tiny covers are rejected rather than upscaled, and a rejected batch leaves no orphan files on disk.
- Animated GIF input keeps its frames and is served as animated WebP; PNG/JPEG/greyscale/RGBA/palette/GIF/WebP
  are the accepted set, while BMP, TIFF, ICO, SVG, HTML and garbage are refused.
- Over-cap screenshot batches, dropped media on save, replaced covers and project deletion each remove exactly the right files.
- Invalid `cover_style` rejected on POST and PUT; reserved usernames and weak passwords rejected on register.
- Invisible bidi/control characters never reach storage; tag dedupe ignores case; search survives unicode and LIKE wildcards.
- `/api/makers/{username}` returns real counts or an honest 404 instead of a fabricated profile.
- Content types (`image/webp`, `text/javascript`), encoded traversal attempts, trailing-slash canonical redirects, `/static/` and `/uploads/` 404s.
- **The canonical redirect cannot be turned into an open redirect:** `//evil.example/` answers `Location: /evil.example`,
  never `//evil.example`, so the trailing-slash helper can never hand the browser a protocol-relative target.
- **One whole animation, not one frame, is pixel-capped.** Pillow only counts pixels per frame, so 40 large frames
  inside a 5 MB file would still ask for far more memory than the static-image guard allows; the budget is now
  checked while decoding and a rejected animation leaves no file behind.
- **Cache policy by how often a URL can change:** `no-store` for every API answer, `immutable` for the
  content-addressed `/uploads/` and `/fonts/` files, and `no-cache` for the shell, every HTML route and
  un-hashed `app.js`/`styles.css` — including `/p/{slug}` and `/explore`, which previously got no header at all.
- **A missing asset is a 404, never the app shell:** `/does-not-exist.js`, `/fonts/ghost.woff2`,
  `/uploads/ghost.webp` and friends answer 404 JSON instead of 200 `text/html` that a browser would cache as
  if it were the file. Extension-less paths still get the shell so the client router can own them.
- Browse, search, filter, sort, pagination bounds, project detail, likes/unlikes.
- Idea submissions requiring neither repository nor demo URL.
- Owner-only modification and deletion; missing-project behavior.
- Reject invalid fields, URL schemes, embedded credentials, SVG/corrupt uploads, and oversize files.
- Reject cross-origin writes with mismatched Origin.
- Source-level guards: the load-more append uses the rendered grid, no `@import` of remote fonts, `@keyframes`
  exist, and the router ends in a real not-found page instead of falling through to the explore grid.

## Browser UI smoke

`tests/browser_flow.py` boots a **real Uvicorn server on a free port** and drives it with Chromium against `http://127.0.0.1:<port>`, so the CSP, cookies and redirects exercised are the production ones. `UNIVORYA_HEADED=1` runs the identical script in a visible window. It verifies:

1. 24 demo cards render, self-hosted variable fonts are registered (`document.fonts.size >= 4`) and the h1 resolves to Plus Jakarta Sans.
2. The motion layer is live: `@keyframes` rules counted in the stylesheet plus running Web Animations, and scroll reveals marking cards visible.
3. `/?q=Orbit&stage=Beta` survives a reload, and Show more appends page 2 (24 → 28 cards).
4. Zero horizontal overflow at 320, 390, 768 and 1024 px.
5. Mobile drawer: opens, sets `aria-expanded`, traps Tab, closes on Escape and outside click, locks body scroll.
6. Password strength meter scores weak vs strong input.
7. Cover and screenshot pickers show real dimensions and byte size; a session expiring mid-submit keeps the typed draft **and** the queued images through re-login.
8. Published project renders its cover plus screenshot gallery, lightbox arrows, caption, and Escape-to-close.
9. Like, public search, owner edit that drops one screenshot, maker profile counters, and the 404 state.
10. A second edit that removes the **last** stored screenshot: the tile flips to restorable, and after saving the
    detail page has no screenshots left while the cover survives — this is the case that silently kept the
    gallery before the `keep_media` presence fix.
11. Unknown route `/a-path-nobody-wrote` renders "Nothing lives at this address." with the matching document
    title, and no project cards.
12. A malformed percent-encoded path (`/p/%zz-not-a-slug`) is decoded defensively and still paints a real view
    instead of throwing and leaving the shell empty.
13. Signed-out gates: `/submit` shows the join-or-sign-in card rather than a form that cannot save, and
    `/dashboard` redirects to `/login` without leaking a dashboard.
14. **Category sweep through the real form**: one project per category in `CATEGORIES` (Web App, Mobile App,
    AI & ML, Dev Tool, Open Source, Design, Idea, Other) is published with the cover and screenshot pickers,
    then the rows are read back **straight out of SQLite** — 8 projects, each with exactly one `cover` row and
    the expected number of `screenshot` rows, with the stage the form was given. Nothing here goes through the
    API helper, so a category that only worked over HTTP would still fail this step.
15. The motion layer is then scrolled through on the creator's own surfaces (dashboard, own maker profile, a
    category-filtered explore, a swept project page) and every `.reveal` element must reach full opacity —
    a card that never animates in is invisible, not merely plain.
16. Each stored tile in the edit form carries the timestamp of its own `project_media` row (`<time>` with an ISO
    `datetime` attribute), so the creator sees kind, pixel size, weight, animation and upload date per file.
17. `prefers-reduced-motion: reduce` shows every card at full opacity with zero running animations.
18. No uncaught page exceptions, and no console errors except the deliberately provoked 401 and the three 404s
    the flow asks for.

The test writes `home-desktop.png`, `home-mobile.png`, `explore-pagination.png`, `upload-tile.png`,
`submission-form.png`, `published-project.png`, `lightbox.png`, `gallery-cleared.png`, `maker-profile.png`,
`not-found.png` and `reduced-motion.png` in this folder. `live-home.png` and `live-detail.png` are manual
captures of a separately started preview instance; the suite does not regenerate them. `docs/screens/*.png` are
top-of-page crops of the full-page captures, made only so the README gallery renders at readable heights; they
are derived from the files above and are not separate observations.

## Adversarial probe rounds

Four scripted probe rounds ran against throwaway `UNIVORYA_DATA_DIR` instances (never `data/`), each round
re-checking the previous round's fixes before pushing into ground they had not covered. The fifth round was the
pre-commit review below. The scripts were scratch tooling
in `/tmp`; every finding they surfaced is now a permanent case in `tests/`.

**Real bugs found and fixed**

| Round | Finding | Fix |
|---|---|---|
| 1 | `edit.html`-style dead paths, drafts lost on refresh, `location` clobbering, CSP-blocked remote font | Frontend rework; fonts self-hosted under `static/fonts/` |
| 1 | Uploaded images were only a string on the project row; nothing was queryable | `project_media` table, one row per file, positions renumbered on prune |
| 2 | The per-client login bucket was checked but never written, so the credential-stuffing cap could not trip | `record_attempt()` fills the account bucket and the client bucket |
| 3 | Registration paid a scrypt hash for every anonymous attempt with no ceiling | Signup attempts capped per client |
| 3 | Anonymous writes with a malformed body returned 422 (a schema map) before the 401 | Auth moved into a route dependency, which FastAPI resolves before body parsing |
| 4 | **Removing every screenshot in the editor silently kept them** — the browser's `keep_media=""` arrives as `None`, identical to omitting the field | A `keep_media` presence dependency reads the parsed form, so omitted and empty now mean different things |
| 4 | A 0-byte file selected by the user vanished without feedback | Rejected with a message naming the file |
| 4 | HTML routes (`/explore`, `/p/{slug}`, `/dashboard`) shipped no `Cache-Control` at all | Middleware now defaults every non-`/api`, non-content-addressed response to `no-cache` |
| 4 | A typo'd asset URL returned 200 with the app shell, which browsers cache as if it were the file | Extension-bearing misses are real 404s |
| 4 | Unknown client routes fell through to the explore grid, so a stale or mistyped link looked like a working page | `notFoundPage()` with its own heading and document title |
| 5 (pre-commit review) | **The new trailing-slash redirect was an open redirect:** `/x/` built its target as `"/" + path.rstrip("/")`, so `//evil.example/` answered `Location: //evil.example` and the browser left our origin | Built with `strip("/")`, plus a case asserting no `Location` may start with `//` or a scheme |
| 5 (pre-commit review) | An animation was capped per frame, not per upload, so one ≤5 MB file could ask Pillow to decode far more than the static-image limit | A whole-animation pixel budget enforced during the frame loop |
| 5 (pre-commit review) | The backend-down screen's Retry used an inline `onclick`, which our own `script-src 'self'` silently refuses — a dead button on the one page whose job is recovery | Routed through the delegated `data-action` handler |
| 5 (pre-commit review) | `decodeURIComponent` on a malformed path (`/p/%zz`) threw and left the SPA blank | A `dec()` helper that falls back to the raw segment |

**Probe bugs, not app bugs** (recorded so the same false positives are not re-chased): passing `files` as a
dict-of-list to httpx 0.28 (it needs a list of tuples, which is also what the browser sends), asserting on a
response from a helper that returns a slug string, checking cache headers before registering a session, a
comprehension that iterated a float, waiting for `.project-card` on a search that is legitimately empty,
submitting the publish form from a signed-out context (that path is gated by design), decorative glow divs
wider than the viewport (the page itself does not scroll sideways), and counting the deliberately hostile
slug's own 404 as a broken request.

## Not claimed

No GitHub CI success until code is pushed; no live production deployment, public availability, multi-browser
matrix, full WCAG audit, load tests or penetration test.

**The throttle map is an MVP guard, and its accuracy depends entirely on how the app is deployed.** It is
in-memory and per process, so multiple workers each hold their own counters and a restart clears them. It keys
on `request.client.host`, and the Dockerfile runs Uvicorn with `--proxy-headers` without
`--forwarded-allow-ips`, which defaults to trusting only `127.0.0.1`:

- `docker compose` with port 8000 published directly — a remote client's own `X-Forwarded-For` is ignored, so the
  buckets cannot be spoofed; but every remote client can collapse onto one shared bridge/NAT address, which means
  one attacker burning 30 logins locks that whole population out for ten minutes. Size these caps per deployment.
- Behind a same-host reverse proxy (`127.0.0.1`) — Uvicorn *does* trust the header, and it takes the **leftmost**
  value, which is attacker-controlled. A deployment like this must pass `--forwarded-allow-ips` for the real proxy
  address and configure the proxy to *overwrite* (not append to) `X-Forwarded-For`, or the per-client caps above
  can be sidestepped at will. The per-**account** cap is keyed on the identifier and still holds either way.

Password strength, signup caps and Origin checks are MVP defenses, not a substitute for the Phase 2 tasks in
[REQUIREMENTS.md](REQUIREMENTS.md). The vendored `static/fonts/*.woff2` subsets (Plus Jakarta Sans, DM Sans) are
both OFL-licensed and still need an attribution file before distribution.
