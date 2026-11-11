"""Google Sign-In coverage: state/PKCE, new accounts, linking and refusals.

The provider conversation is stubbed at the two module boundaries, so these tests
prove Univorya's half of the handshake - exactly what runs against Google - without
depending on a live account.  ``scripts/dev_mock_google.py`` exercises the same
code through a real browser against a local identity provider.
"""
import importlib
import io
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from PIL import Image

PROFILE = {
    "sub": "109876543210987654321",
    "email": "aarav.google.demo@gmail.com",
    "name": "Aarav Google Demo",
    "picture": "https://example.invalid/avatar.png",
}


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVORYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "0")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "demo-client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "demo-secret")
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        client.main = main
        yield client


def stub_provider(monkeypatch, main, profile=None):
    person = dict(profile or PROFILE)
    monkeypatch.setattr(main.google_oauth, "exchange_code",
                        lambda cfg, code, verifier: f"access-token:{code}")
    monkeypatch.setattr(main.google_oauth, "fetch_profile",
                        lambda cfg, token: dict(person))
    return person


def begin(client, next_path="/dashboard"):
    response = client.get("/auth/google/start", params={"next": next_path},
                          follow_redirects=False)
    assert response.status_code == 302, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    return response, query


def finish(client, code="one-time-code", state=None):
    state = state or client.cookies.get("univorya_oauth_state")
    return client.get("/auth/google/callback", params={"code": code, "state": state},
                      follow_redirects=False)


def png(width=640, height=440):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 220)).save(buffer, "PNG")
    return buffer.getvalue()


def test_start_sends_state_and_pkce_to_google(c):
    response, query = begin(c, "/submit")
    location = urlparse(response.headers["location"])
    assert location.scheme == "https" and location.netloc == "accounts.google.com"
    assert query["client_id"] == ["demo-client.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["http://testserver/auth/google/callback"]
    assert query["response_type"] == ["code"]
    assert "email" in query["scope"][0] and "profile" in query["scope"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["state"][0] == c.cookies.get("univorya_oauth_state")
    # The verifier never appears in the redirect; only its S256 digest does.
    assert c.cookies.get("univorya_oauth_verifier")
    # http.cookies quotes values containing '/', so strip the transport quotes.
    assert c.cookies.get("univorya_oauth_next").strip('"') == "/submit"


def test_callback_creates_google_account_and_session(c, monkeypatch):
    stub_provider(monkeypatch, c.main)
    _, query = begin(c)
    response = finish(c, state=query["state"][0])
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"
    assert response.cookies.get("univorya_session")

    me = c.get("/api/auth/me").json()["user"]
    assert me["email"] == PROFILE["email"]
    assert me["name"] == "Aarav Google Demo"
    assert me["username"] == "aarav_google_demo" or me["username"].startswith("aarav")
    assert me["auth_provider"] == "google"

    main = c.main
    with main.db() as con:
        row = con.execute("SELECT * FROM users").fetchone()
        assert row["google_sub"] == PROFILE["sub"]
        assert row["auth_provider"] == "google"
        assert row["password_hash"].count(":") == 1  # a real scrypt hash, not plaintext
        assert PROFILE["email"] not in (row["password_hash"] or "")


def test_second_google_login_reuses_the_same_account(c, monkeypatch):
    stub_provider(monkeypatch, c.main)
    for _ in range(2):
        begin(c)
        assert finish(c).status_code == 302
    me = c.get("/api/auth/me").json()["user"]
    with c.main.db() as con:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM sessions WHERE expires > 0").fetchone()[0] == 2
    assert me["auth_provider"] == "google"


def test_verified_email_links_an_existing_password_account(c, monkeypatch):
    signup = c.post("/api/auth/register", json={
        "name": "Aarav Before Google", "username": "aarav_existing",
        "email": PROFILE["email"], "password": "Correct-Horse-59"})
    assert signup.status_code == 201, signup.text
    before = signup.json()["user"]
    c.post("/api/auth/logout")

    stub_provider(monkeypatch, c.main)
    begin(c)
    assert finish(c).status_code == 302

    me = c.get("/api/auth/me").json()["user"]
    assert me["id"] == before["id"]
    assert me["username"] == "aarav_existing"
    assert me["auth_provider"] == "google"
    with c.main.db() as con:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_google_created_account_cannot_use_the_password_form(c, monkeypatch):
    stub_provider(monkeypatch, c.main)
    begin(c)
    finish(c)
    c.post("/api/auth/logout")
    denied = c.post("/api/auth/login", json={"email": PROFILE["email"], "password": "anything-123"})
    assert denied.status_code == 401
    assert "Continue with Google" in denied.json()["detail"]


def test_google_user_can_publish_a_project(c, monkeypatch):
    stub_provider(monkeypatch, c.main)
    begin(c)
    finish(c)
    created = c.post("/api/projects", data={
        "title": "Google Sign-In Demo",
        "tagline": "Published by an account created with Google OAuth.",
        "description": "This project proves the Google-created account is a normal maker: it owns projects and likes.",
        "category": "Dev Tool", "stage": "Beta", "cover_style": "mint",
    }, files=[("cover", ("cover.png", png(), "image/png"))])
    assert created.status_code == 201, created.text
    detail = c.get(f"/api/projects/{created.json()['slug']}").json()["project"]
    assert detail["creator"]["username"].startswith("aarav")
    assert detail["is_owner"] is True


@pytest.mark.parametrize("target", ["/dashboard", "/submit?draft=1", "/p/google-demo"])
def test_legitimate_next_target_survives(c, monkeypatch, target):
    stub_provider(monkeypatch, c.main)
    _, query = begin(c, target)
    response = finish(c, state=query["state"][0])
    assert response.headers["location"] == target


def test_hostile_next_cannot_escape(c, monkeypatch):
    stub_provider(monkeypatch, c.main)
    _, query = begin(c, "//evil.example/phish")
    response = finish(c, state=query["state"][0])
    assert response.headers["location"] == "/dashboard"


def test_state_mismatch_is_refused(c, monkeypatch):
    stub_provider(monkeypatch, c.main)
    begin(c)
    response = finish(c, state="not-the-state")
    assert response.status_code == 302
    assert response.headers["location"] == "/login?google_error=state"
    assert not c.cookies.get("univorya_session")


def test_provider_cancel_is_friendly(c):
    _, query = begin(c)
    response = c.get("/auth/google/callback",
                     params={"error": "access_denied", "state": query["state"][0]},
                     follow_redirects=False)
    assert response.headers["location"] == "/login?google_error=denied"


def test_failed_exchange_does_not_create_a_session(c, monkeypatch):
    def boom(cfg, code, verifier):
        raise c.main.google_oauth.GoogleOAuthError("provider returned HTTP 400")
    monkeypatch.setattr(c.main.google_oauth, "exchange_code", boom)
    _, query = begin(c)
    response = finish(c, state=query["state"][0])
    assert response.headers["location"] == "/login?google_error=exchange"
    assert not c.cookies.get("univorya_session")
    with c.main.db() as con:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_unverified_email_is_refused(monkeypatch):
    from app import google_oauth
    payload = {"sub": "1", "email": "unverified@example.com", "email_verified": False,
               "name": "Unverified"}
    monkeypatch.setattr(google_oauth, "_read_json", lambda request: payload)
    cfg = google_oauth.GoogleConfig("id", "secret", "http://localhost/callback")
    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.fetch_profile(cfg, "token")


def test_exchange_code_posts_pkce_and_client_secret(monkeypatch):
    from urllib.parse import parse_qs
    from app import google_oauth
    captured = {}

    def fake_read(request):
        captured["method"] = request.get_method()
        captured["body"] = request.data.decode()
        return {"access_token": "token-123"}

    monkeypatch.setattr(google_oauth, "_read_json", fake_read)
    cfg = google_oauth.GoogleConfig("id", "secret", "http://localhost/auth/google/callback",
                                    token_url="https://token.example/exchange")
    assert google_oauth.exchange_code(cfg, "one-time", "verifier-value") == "token-123"
    form = parse_qs(captured["body"])
    assert captured["method"] == "POST"
    assert form["code"] == ["one-time"]
    assert form["code_verifier"] == ["verifier-value"]
    assert form["client_secret"] == ["secret"]
    assert form["grant_type"] == ["authorization_code"]


def test_exchange_code_requires_an_access_token(monkeypatch):
    from app import google_oauth
    monkeypatch.setattr(google_oauth, "_read_json", lambda request: {"token_type": "Bearer"})
    cfg = google_oauth.GoogleConfig("id", "secret", "http://localhost/callback")
    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.exchange_code(cfg, "code", "verifier")


def test_fetch_profile_normalises_google_payload(monkeypatch):
    from app import google_oauth
    payload = {"sub": " 42 ", "email": " Aarav@Example.COM ", "name": "Aarav",
               "email_verified": "true", "picture": "https://example.invalid/p.png"}
    monkeypatch.setattr(google_oauth, "_read_json", lambda request: payload)
    cfg = google_oauth.GoogleConfig("id", "secret", "http://localhost/callback")
    profile = google_oauth.fetch_profile(cfg, "token")
    assert profile == {"sub": "42", "email": "aarav@example.com", "name": "Aarav",
                       "picture": "https://example.invalid/p.png"}


def test_read_json_handles_a_real_http_round_trip():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import Request
    from app import google_oauth

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/bad":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not json")
            elif self.path == "/500":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error":"boom"}')
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert google_oauth._read_json(Request(base + "/ok")) == {"ok": True}
        with pytest.raises(google_oauth.GoogleOAuthError):
            google_oauth._read_json(Request(base + "/500"))
        with pytest.raises(google_oauth.GoogleOAuthError):
            google_oauth._read_json(Request(base + "/bad"))
    finally:
        server.shutdown()
        server.server_close()


def test_safe_next_only_allows_local_paths():
    from app.main import safe_next
    assert safe_next("/submit") == "/submit"
    assert safe_next("/p/a?x=1") == "/p/a?x=1"
    for hostile in ("", "https://evil.example", "//evil.example", "\\\\evil.example",
                    "/\\evil.example", "javascript:alert(1)", "/ok\nSet-Cookie: x=1"):
        assert safe_next(hostile) == "/dashboard"


def test_disabled_without_credentials(tmp_path, monkeypatch):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UNIVORYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "0")
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        assert client.get("/api/metadata").json()["google"]["enabled"] is False
        assert client.get("/auth/google/start", follow_redirects=False).status_code == 503


def test_memorydb_backend_signs_in_and_links(tmp_path, monkeypatch, fake_memorydb):
    monkeypatch.setenv("UNIVORYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "0")
    monkeypatch.setenv("UNIVORYA_STORE", "memorydb")
    monkeypatch.setenv("MEMORYDB_URL", fake_memorydb.url)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "demo-client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "demo-secret")
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        monkeypatch.setattr(main.google_oauth, "exchange_code",
                            lambda cfg, code, verifier: "access-token")
        monkeypatch.setattr(main.google_oauth, "fetch_profile",
                            lambda cfg, token: dict(PROFILE))
        for _ in range(2):
            started = client.get("/auth/google/start", follow_redirects=False)
            state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
            done = client.get("/auth/google/callback",
                              params={"code": "c", "state": state}, follow_redirects=False)
            assert done.headers["location"] == "/dashboard"

        user = main.MEMORY.find_user_by_google_sub(PROFILE["sub"])
        assert user["auth_provider"] == "google"
        assert user["email"] == PROFILE["email"]
        assert main.MEMORY.community_counts()["makers"] == 1

        client.post("/api/auth/logout")
        link = client.post("/api/auth/register", json={
            "name": "Link Me", "username": "link_me", "email": "link@example.com",
            "password": "Correct-Horse-59"})
        assert link.status_code == 201, link.text
        client.post("/api/auth/logout")

        monkeypatch.setattr(main.google_oauth, "fetch_profile",
                            lambda cfg, token: {**PROFILE, "sub": "999", "email": "link@example.com"})
        started = client.get("/auth/google/start", follow_redirects=False)
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        client.get("/auth/google/callback", params={"code": "c", "state": state},
                   follow_redirects=False)
        me = client.get("/api/auth/me").json()["user"]
        assert me["username"] == "link_me"
        assert me["auth_provider"] == "google"
        assert main.MEMORY.community_counts()["makers"] == 2
