# Univorya — Local Test Results

**Run date:** 19 October 2026

| Test suite | Command | Result |
|---|---|---|
| API and authorization | `pytest --cov=app -q` | **16 passed, 89% backend coverage** |
| JS syntax | `node --check static/app.js` | **Passed** |
| Browser UI smoke | `python tests/browser_flow.py` | **Passed** |
| Real HTTP integration | `python scripts/smoke_http.py` against local Uvicorn | **Passed** |

## API cases covered

- Health and static assets; UI branding/security headers.
- Register, login, duplicate account, password validation, logout, HttpOnly cookies.
- Actual PNG upload, image conversion to WebP, public file serving.
- Browse, search, filter, sort, and pagination.
- Creator profile, project detail, likes/unlikes.
- Idea submissions requiring neither repository nor demo URL.
- Update title/category/stage/links preserving project URL and existing image.
- Replace image and remove old file.
- Owner-only modification and deletion; missing-project behavior.
- Reject invalid fields, URL schemes, embedded credentials, SVG/corrupt uploads, and oversize files.
- Reject cross-origin writes with mismatched Origin.

## Browser UI smoke

Headless Chromium loads the **actual** `static/styles.css` and `static/app.js`. Because this sandbox disallows direct browser localhost networking, browser `fetch` requests are transported to FastAPI TestClient. Image contents come from the real WebP storage. The test verifies:

1. Desktop and 390px mobile layout (no mobile horizontal overflow).
2. Registration form and signed-out submit gate.
3. Actual image input selection, submission and public project detail rendering.
4. Search finds the newly submitted project.
5. Owner opens Edit, changes title and stage, saves, sees the changes publicly.
6. No JavaScript page exceptions during the test.

The test outputs `home-desktop.png`, `home-mobile.png`, `submission-form.png`, `published-project.png` in this folder.

## Not claimed

No GitHub CI success until code is pushed; no live production deployment, public availability, multi-browser matrix, full WCAG audit, load tests or penetration test. Production security and operational readiness need the Phase 2 tasks in [REQUIREMENTS.md](REQUIREMENTS.md).
