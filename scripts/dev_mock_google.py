"""A tiny local stand-in for Google's OAuth 2.0 + OpenID Connect endpoints.

DEVELOPMENT / DEMO ONLY.  It speaks the same wire protocol as Google, so the app's
real /auth/google/start and /auth/google/callback code runs unchanged, including
the authorization-code exchange with PKCE.  It is never imported by the app and is
never part of the production image.

    python -m uvicorn scripts.dev_mock_google:app --host 127.0.0.1 --port 8099

Then point the app at it:

    GOOGLE_CLIENT_ID=univorya-local-demo.apps.googleusercontent.com
    GOOGLE_CLIENT_SECRET=local-demo-secret
    GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
    GOOGLE_AUTH_URL=http://127.0.0.1:8099/o/oauth2/v2/auth
    GOOGLE_TOKEN_URL=http://127.0.0.1:8099/token
    GOOGLE_USERINFO_URL=http://127.0.0.1:8099/userinfo

Demo account (this file only, not a Google account):
    aarav.google.demo@gmail.com / GoogleDemo-2026!
"""
from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="Local Google identity provider (demo only)", docs_url=None)

CLIENT_ID = os.environ.get("MOCK_GOOGLE_CLIENT_ID", "univorya-local-demo.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("MOCK_GOOGLE_CLIENT_SECRET", "local-demo-secret")
DEMO_EMAIL = os.environ.get("MOCK_GOOGLE_EMAIL", "aarav.google.demo@gmail.com")
DEMO_PASSWORD = os.environ.get("MOCK_GOOGLE_PASSWORD", "GoogleDemo-2026!")
DEMO_SUB = os.environ.get("MOCK_GOOGLE_SUB", "109876543210987654321")
DEMO_NAME = os.environ.get("MOCK_GOOGLE_NAME", "Aarav Google Demo")
CODE_TTL = 300
CODES: dict[str, dict] = {}
TOKENS: dict[str, dict] = {}


def _allowed_redirect(uri: str) -> bool:
    parsed = urlparse(uri)
    return (parsed.scheme in ("http", "https")
            and parsed.hostname in ("127.0.0.1", "localhost", "testserver")
            and parsed.path == "/auth/google/callback")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in - Google Accounts (local demo)</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
background:#f1f3f4;font-family:Roboto,Arial,sans-serif;color:#202124}}
.card{{width:min(420px,calc(100% - 32px));background:#fff;border:1px solid #dadce0;border-radius:12px;
padding:40px 40px 32px;box-shadow:0 1px 3px rgba(60,64,67,.15)}}
.brand{{font-size:22px;font-weight:600;letter-spacing:-.2px;margin-bottom:6px}}
.brand span:nth-child(1){{color:#4285F4}}.brand span:nth-child(2){{color:#EA4335}}
.brand span:nth-child(3){{color:#FBBC05}}.brand span:nth-child(4){{color:#4285F4}}
.brand span:nth-child(5){{color:#34A853}}.brand span:nth-child(6){{color:#EA4335}}
h1{{font-size:22px;font-weight:400;margin:14px 0 6px}}p.sub{{margin:0 0 26px;color:#5f6368;font-size:14px;line-height:1.5}}
label{{display:block;font-size:13px;color:#5f6368;margin:0 0 6px}}
input{{width:100%;height:48px;padding:0 14px;border:1px solid #dadce0;border-radius:6px;font-size:15px;outline:0}}
input:focus{{border-color:#1a73e8;box-shadow:0 0 0 2px rgba(26,115,232,.15)}}
.field{{margin-bottom:18px}}button{{width:100%;height:44px;border:0;border-radius:6px;background:#1a73e8;color:#fff;
font-size:14px;font-weight:600;cursor:pointer;margin-top:8px}}button:hover{{background:#1765cc}}
.note{{margin-top:22px;padding-top:16px;border-top:1px solid #e8eaed;color:#80868b;font-size:11px;line-height:1.5}}
.error{{background:#fce8e6;color:#c5221f;border-radius:6px;padding:10px 12px;font-size:13px;margin-bottom:18px}}
.avatar{{width:40px;height:40px;border-radius:50%;background:#7b61ff;color:#fff;display:grid;place-items:center;
font-weight:700;margin-bottom:14px}}
</style></head><body><div class="card">
<div class="brand"><span>G</span><span>o</span><span>o</span><span>g</span><span>l</span><span>e</span></div>
{body}
<div class="note">Local OAuth demo provider - not affiliated with Google. It speaks the same
authorization-code + PKCE protocol so Univorya's real callback can be tested offline.</div>
</div></body></html>""")


@app.get("/", response_class=HTMLResponse)
def index():
    return _page("<h1>Univorya demo identity provider</h1>"
                 "<p class='sub'>This server exists so Google sign-in can be tested end to end "
                 "without a Google Cloud project. Point GOOGLE_AUTH_URL here.</p>")


@app.get("/o/oauth2/v2/auth", response_class=HTMLResponse)
def authorize(request: Request):
    params = {key: request.query_params.get(key, "") for key in (
        "client_id", "redirect_uri", "response_type", "scope", "state",
        "code_challenge", "code_challenge_method")}
    if params["client_id"] != CLIENT_ID:
        return _page("<h1>Unknown client</h1><p class='sub'>The client_id does not match.</p>")
    if not _allowed_redirect(params["redirect_uri"]):
        return _page("<h1>Redirect refused</h1><p class='sub'>This demo only returns to "
                     "127.0.0.1/localhost /auth/google/callback.</p>")
    if params["response_type"] != "code" or params["code_challenge_method"] != "S256":
        return _page("<h1>Unsupported request</h1><p class='sub'>Only code + PKCE S256 is supported.</p>")
    hidden = "".join(f'<input type="hidden" name="{key}" value="{html.escape(value)}">'
                     for key, value in params.items())
    return _page(f"""<h1>Sign in with Google</h1>
<p class="sub">to continue to <strong>Univorya</strong></p>
<form method="post" action="/o/oauth2/v2/auth">{hidden}
<div class="field"><label for="email">Email</label>
<input id="email" name="email" type="email" value="{html.escape(DEMO_EMAIL)}" required></div>
<div class="field"><label for="password">Password</label>
<input id="password" name="password" type="password" placeholder="Demo password" required></div>
<button type="submit">Sign in</button></form>""")


@app.post("/o/oauth2/v2/auth")
async def authorize_submit(request: Request):
    form = await request.form()
    redirect_uri = str(form.get("redirect_uri", ""))
    client_id = str(form.get("client_id", ""))
    state = str(form.get("state", ""))
    challenge = str(form.get("code_challenge", ""))
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    if client_id != CLIENT_ID or not _allowed_redirect(redirect_uri):
        return _page("<h1>Request refused</h1>")
    if email != DEMO_EMAIL or not secrets.compare_digest(password, DEMO_PASSWORD):
        hidden = "".join(f'<input type="hidden" name="{k}" value="{html.escape(str(form.get(k, "")))}">'
                         for k in ("client_id", "redirect_uri", "response_type", "scope", "state",
                                   "code_challenge", "code_challenge_method"))
        return _page(f'<div class="error">Wrong email or password. Use the demo account shown in the terminal.</div>'
                     f'<form method="post" action="/o/oauth2/v2/auth">{hidden}'
                     f'<div class="field"><label for="email">Email</label>'
                     f'<input id="email" name="email" type="email" value="{html.escape(email)}" required></div>'
                     f'<div class="field"><label for="password">Password</label>'
                     f'<input id="password" name="password" type="password" required></div>'
                     f'<button type="submit">Sign in</button></form>')
    now = time.time()
    for code, grant in list(CODES.items()):
        if grant["expires"] < now:
            CODES.pop(code, None)
    code = secrets.token_urlsafe(32)
    CODES[code] = {"challenge": challenge, "redirect_uri": redirect_uri,
                   "email": email, "expires": now + CODE_TTL}
    separator = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{separator}{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(location, status_code=302)


@app.post("/token")
async def token(request: Request):
    form = await request.form()
    code = str(form.get("code", ""))
    verifier = str(form.get("code_verifier", ""))
    grant = CODES.pop(code, None)
    if (not grant or grant["expires"] < time.time()
            or str(form.get("client_id", "")) != CLIENT_ID
            or not secrets.compare_digest(str(form.get("client_secret", "")), CLIENT_SECRET)
            or str(form.get("redirect_uri", "")) != grant["redirect_uri"]
            or str(form.get("grant_type", "")) != "authorization_code"):
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "code, secret or redirect mismatch"}, status_code=400)
    digest = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    if not verifier or not secrets.compare_digest(digest, grant["challenge"]):
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "PKCE verification failed"}, status_code=400)
    access_token = secrets.token_urlsafe(40)
    TOKENS[access_token] = {"sub": DEMO_SUB, "email": grant["email"], "name": DEMO_NAME,
                            "email_verified": True,
                            "picture": "https://example.invalid/avatar.png",
                            "expires": time.time() + 3600}
    return {"access_token": access_token, "expires_in": 3600, "token_type": "Bearer",
            "scope": "openid email profile"}


@app.get("/userinfo")
def userinfo(request: Request):
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    profile = TOKENS.get(token)
    if not profile or profile["expires"] < time.time():
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    return {key: profile[key] for key in ("sub", "email", "email_verified", "name", "picture")}
