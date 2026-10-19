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
| U-03 | Upload an image | PNG/JPG/WebP/GIF up to 5MB; decoded and stored as WebP; rejects invalid files | Implemented; tested |
| U-04 | Public showcase page | Stable URL with title, creator, description, stack, links, cover | Implemented; tested |
| U-05 | Discovery | Keyword search, category/stage filtering, ordering and pagination | Implemented; API tested |
| U-06 | Creator portfolio | Public list of projects and self-dashboard | Implemented; tested |
| U-07 | Appreciation | Logged-in visitor toggles like once per project | Implemented; tested |
| U-08 | Edit project | Only owner may edit; same slug preserved; optional cover replacement | Implemented; API/browser tested |
| U-09 | Delete project | Only owner may delete; image removed | Implemented; tested |
| U-10 | Responsive UI | Desktop/mobile, transparent glass panels, no horizontal scrolling at 390px | Implemented; browser tested |
| U-11 | Transparent samples | Fictional seed cards clearly labeled; disabling via env | Implemented; tested |
| U-12 | CI/testing | pytest, JS syntax check, Chromium interaction smoke, screenshots | Configured; local tests run; CI awaits repo |

## Explicit non-goals of MVP

No native APK/IPA signing or distribution, no source-control/git hosting, no running arbitrary uploaded code, no AI-model inference hosting, no paid marketplace/payments, no hiring/recruiting marketplace, no built-in messaging/chat, no public user-generated comments, no mobile native client.

## Quality/security requirements

- Validate values on server, not just browser; use parameterized SQL and escaped rendered user text.
- Passwords are hashed with scrypt; bearer token stored only as hashed digest in SQLite, cookie HttpOnly/SameSite.
- Restrict writable project operations to authenticated owner. Enforce website URL scheme checks.
- Reject files that aren't decodeable allowed images; strip metadata and resize.
- Add basic browser hardening headers and reject mismatched Origin headers on write requests.
- Display real/synthetic creator content distinctly: demo cards **never** imply real users.
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

**Passed locally:** `pytest` (16 API tests), Chromium browser smoke (signup → image upload → publish → search → edit), real HTTP smoke script when a server is running. **Not passed/verified:** GitHub-hosted CI run, live deployment, production-domain TLS, managed storage, anti-abuse and full a11y/security audit.
