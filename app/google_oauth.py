"""Google Sign-In: OAuth 2.0 authorization-code flow with PKCE.

The module is deliberately small and uses only the standard library, so the image
needs no extra package.  It builds the consent redirect, exchanges the one-time
code and reads the OpenID profile; account creation, linking and the session
cookie stay in :mod:`app.main` so Google and password logins share one code path.

Enable it with ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET``.  The three
endpoint variables exist for the test suite and for the local demo identity
provider in ``scripts/dev_mock_google.py``; production keeps the defaults.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPE = "openid email profile"
TIMEOUT = 15


class GoogleOAuthError(Exception):
    """A failed handshake with the identity provider.

    Messages are short, fixed strings: they are safe to map to a redirect code and
    never echo a provider payload back to the browser.
    """


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    auth_url: str = AUTH_URL
    token_url: str = TOKEN_URL
    userinfo_url: str = USERINFO_URL


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID", "").strip()
                and os.environ.get("GOOGLE_CLIENT_SECRET", "").strip())


def config(default_redirect_uri: str) -> GoogleConfig | None:
    """Read the environment on every call so a restart, not a code change, turns Google on."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return GoogleConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.environ.get("GOOGLE_REDIRECT_URI", "").strip() or default_redirect_uri,
        auth_url=os.environ.get("GOOGLE_AUTH_URL", "").strip() or AUTH_URL,
        token_url=os.environ.get("GOOGLE_TOKEN_URL", "").strip() or TOKEN_URL,
        userinfo_url=os.environ.get("GOOGLE_USERINFO_URL", "").strip() or USERINFO_URL,
    )


def new_pkce() -> tuple[str, str]:
    """Return ``(verifier, S256 challenge)`` as RFC 7636 describes them."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_url(cfg: GoogleConfig, state: str, challenge: str) -> str:
    query = urlencode({
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    separator = "&" if "?" in cfg.auth_url else "?"
    return f"{cfg.auth_url}{separator}{query}"


def _read_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(64_000)
    except HTTPError as exc:
        raise GoogleOAuthError(f"provider returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise GoogleOAuthError("provider unreachable") from exc
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise GoogleOAuthError("provider returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise GoogleOAuthError("provider returned an unexpected payload")
    return payload


def exchange_code(cfg: GoogleConfig, code: str, verifier: str) -> str:
    """Trade the one-time authorization code for an access token."""
    form = urlencode({
        "code": code,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "redirect_uri": cfg.redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode("ascii")
    request = Request(cfg.token_url, data=form, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    payload = _read_json(request)
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise GoogleOAuthError("provider did not return an access token")
    return token


def fetch_profile(cfg: GoogleConfig, access_token: str) -> dict:
    """Read the OpenID profile and keep only the fields Univorya stores.

    An unverified address is refused: linking by email is only safe when Google
    vouches for the address.
    """
    request = Request(cfg.userinfo_url, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    })
    payload = _read_json(request)
    sub = str(payload.get("sub") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    name = str(payload.get("name") or "").strip()
    verified = payload.get("email_verified")
    if isinstance(verified, str):
        verified = verified.strip().lower() == "true"
    if not sub or len(sub) > 255:
        raise GoogleOAuthError("provider profile has no subject")
    if not email or len(email) > 200 or not verified:
        raise GoogleOAuthError("provider profile has no verified email")
    return {
        "sub": sub,
        "email": email,
        "name": name[:70],
        "picture": str(payload.get("picture") or "")[:500],
    }
