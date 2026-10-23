# Univorya — Product Requirements Document (MVP v0.2)

**Vision:** One home for everything developers and builders create. Users can share original apps, open-source projects and forks, AI tools, designs, prototypes, and ideas. This is **not** a reverse-engineering-only site or a binary app store.

## Personas & primary jobs

| Persona | Job-to-be-done |
|---|---|
| Student / newcomer | Publish a project or idea without needing deployed code, obtain a shareable link |
| Indie developer | Show an app, live demo, code, stack, creator attribution and screenshots |
| AI engineer | Showcase AI/ML tools and model-related projects without uploading model weights |
| Open-source maintainer | Credit contributors; link to licensed repository and documentation |
| Visitor/recruiter | Browse, search, filter, view creator work, appreciate and follow external resources |

## MVP requirements and acceptance criteria

| ID | Requirement | Acceptance criterion | Status |
|---|---|---|---|
| U-01 | Register/login/logout | Email/username validation, password hashed, HttpOnly cookie sessions | Implemented; tested |
| U-02 | Create a project/idea | Title/tagline/description/category/stage, optional repo/demo URL | Implemented; tested |
| U-03 | Upload an image | PNG/JPG/WebP/GIF up to 5MB each, 8 files and 25MB combined, at least 200×150; decoded, stripped of camera/GPS metadata, shrunk but never upscaled, stored as WebP; rejects invalid files | Implemented; tested |
| U-04 | Public showcase page | Stable URL with title, creator, description, stack, links, cover | Implemented; tested |
| U-05 | Discovery | Keyword search, category/stage filtering, ordering and pagination | Implemented; API and browser tested |
| U-06 | Creator portfolio | Public list of projects and self-dashboard | Implemented; tested |
| U-07 | Appreciation | Logged-in visitor toggles like once per project | Implemented; tested |
| U-08 | Edit project | Only owner may edit; same slug preserved; optional cover replacement | Implemented; API/browser tested |
| U-09 | Delete project | Only owner may delete; image removed | Implemented; tested |
| U-10 | Responsive UI | Desktop/mobile, transparent glass panels, no horizontal scrolling at 390px | Implemented; browser tested |
| U-11 | Transparent samples | Fictional seed cards clearly labeled; disabling via env | Implemented; tested |
| U-12 | CI/testing | pytest, JS syntax check, Chromium interaction smoke, screenshots | Implemented; green on GitHub Actions for `main` |

## Explicit non-goals of MVP

No native APK/IPA signing or distribution, no source-control/git hosting, no running arbitrary uploaded code, no AI-model inference hosting, no paid marketplace/payments, no hiring/recruiting marketplace, no built-in messaging/chat, no public user-generated comments, no mobile native client.

## Quality/security requirements

- Validate values on server, not just browser; use parameterized SQL and escaped rendered user text.
- Passwords are hashed with scrypt; bearer token stored only as hashed digest in SQLite, cookie HttpOnly/SameSite.
- Restrict writable project operations to authenticated owner. Enforce website URL scheme checks.
- Reject files that aren't decodeable allowed images; strip metadata and resize. Verified: camera/model/software tags and GPS coordinates do not survive the WebP re-encode, and the animation pixel budget is enforced per upload, not per frame.
- Keep every generated redirect target same-origin — a canonicalisation helper that emits `//host` is an open redirect.
- Add basic browser hardening headers and reject mismatched Origin headers on write requests.
- Display real/synthetic creator content distinctly: demo cards **never** imply real users.
- Show the maker what their own uploads are: every `project_media` row the API returns is rendered with its kind, pixel size, weight, animation flag and upload date, so no stored fact is fetched and then discarded. Verified in Chromium for all eight categories, publishing through the form and reading the rows back out of SQLite.
- SQLite and image directory **must** be persistent if deployed; create backup/restore routine.
- No external hosting or recurring paid resource provisioned without explicit deployment decision.

## Phase 2 (unimplemented; blockers before public scale)

1. Email verification + password reset, anti-spam/captcha, abuse reports, content moderation and takedown workflow.
2. Managed PostgreSQL and object storage (S3-compatible) with signed uploads and CDN; backups and point-in-time recovery.
3. GitHub OAuth / repository ownership verification and creator account linking.
4. Organization teams, collaborator management, draft/unlisted projects and update timelines.
5. Analytics, ranking resistant to gaming, follows/bookmarks, notifications, accessibility audit, legal policies.
6. Structured audits/metrics, centralized rate-limiting, monitoring/alerts and load testing.

## Policy requirements to finalize before launch

- Original work, properly licensed forks, and independently built inspired projects may be submitted.
- No stolen proprietary code, malicious artifacts, impersonation, leaked credentials, copyrighted assets without rights.
- Attribution, community rules, privacy policy, DMCA/IP complaint process, terms of use and jurisdiction-specific legal review.
- **Univorya brand is provisional** until domain and trademark clearance.

## Release gate

**Passed locally:** `pytest --cov=app` (58 API tests, 94% of `app/main.py`), Chromium browser smoke (signup → multi-image upload → publish → search → edit → clearing the gallery → not-found, malformed-URL and signed-out gates → one project per category verified in the database), real HTTP smoke script when a server is running. **Passed on GitHub Actions:** both CI jobs (Python + JS, and the Chromium smoke in Playwright-installed Chromium) on every push to `main`. **Not passed/verified:** live deployment, production-domain TLS, managed storage, anti-abuse and full a11y/security audit.
