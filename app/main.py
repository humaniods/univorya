"""Univorya — a lightweight, real project showcase MVP.
Run: uvicorn app.main:app --reload
"""
from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import unicodedata
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

mimetypes.add_type("image/webp", ".webp")

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from .memory_store import (
    DuplicateRecord,
    MemoryDBError,
    MemoryStore,
    RecordNotFound,
    VersionConflict,
)
from . import google_oauth, studio

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("UNIVORYA_DATA_DIR", str(ROOT / "data")))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "univorya.sqlite3"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
studio.configure(DATA_DIR)

# MemoryDB is the production store. SQLite stays the zero-setup development
# fallback, and is what the existing test suite exercises unless it opts in.
MEMORYDB_URL = os.environ.get("MEMORYDB_URL", "").strip()
STORE_BACKEND = os.environ.get(
    "UNIVORYA_STORE", "memorydb" if MEMORYDB_URL else "sqlite"
).strip().lower()
if STORE_BACKEND not in ("sqlite", "memorydb"):
    raise RuntimeError("UNIVORYA_STORE must be 'sqlite' or 'memorydb'")
MEMORY: MemoryStore | None = None
if STORE_BACKEND == "memorydb":
    if not MEMORYDB_URL:
        raise RuntimeError("UNIVORYA_STORE=memorydb requires MEMORYDB_URL")
    MEMORY = MemoryStore(MEMORYDB_URL)

app = FastAPI(title="Univorya API", version="0.2.0", docs_url="/api/docs", redoc_url=None)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.exception_handler(RecordNotFound)
async def memory_not_found(request: Request, exc: RecordNotFound):
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.exception_handler(DuplicateRecord)
async def memory_duplicate(request: Request, exc: DuplicateRecord):
    return JSONResponse(status_code=409, content={"detail": "That record already exists"})


@app.exception_handler(VersionConflict)
async def memory_conflict(request: Request, exc: VersionConflict):
    return JSONResponse(status_code=409, content={"detail": "This item changed while you were editing. Reload and try again."})


@app.exception_handler(MemoryDBError)
async def memory_unavailable(request: Request, exc: MemoryDBError):
    return JSONResponse(status_code=503, content={"detail": "The showcase database is temporarily unavailable"})


@app.exception_handler(studio.StudioError)
async def studio_error(request: Request, exc: studio.StudioError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

CATEGORIES = ["Web App", "Mobile App", "AI & ML", "Dev Tool", "Open Source", "Design", "Idea", "Other"]
STAGES = ["Idea", "Building", "Beta", "Launched"]
COVER_STYLES = ("violet", "blue", "orange", "mint", "pink", "slate")
ALLOWED_IMAGE_FORMATS = ("PNG", "JPEG", "WEBP", "GIF")
MAX_FILE_BYTES = 5 * 1024 * 1024
MIN_IMAGE_WIDTH = 200
MIN_IMAGE_HEIGHT = 150
MAX_ANIMATED_FRAMES = 40
# Pillow only counts pixels per frame, so a 5 MB animation could still ask for 40 full-size
# decodes. This is the budget for one whole upload, not one frame.
MAX_ANIMATION_PIXELS = 40_000_000
MAX_MEDIA_PER_PROJECT = 8
MAX_TOTAL_UPLOAD_BYTES = 25 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 22_000_000

# Blocked so nobody can squat a profile URL that impersonates staff or a category page.
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "superuser", "staff", "support", "help", "team",
    "official", "univorya", "system", "moderator", "security", "about", "explore",
    "submit", "dashboard", "login", "join", "edit", "makers", "project", "api", "www",
}

# Right-to-left overrides and zero-width/control characters let a title masquerade as
# something it is not, so they never reach storage.
DISALLOWED_CODEPOINTS = {
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069,
    0x200B, 0x200E, 0x200F, 0xFEFF,
}


def clean_text(raw: str) -> str:
    """Normalise a user string: drop invisible overrides/control chars, keep real newlines and emoji ZWJ."""
    text = unicodedata.normalize("NFC", str(raw or ""))
    text = "".join(ch for ch in text
                   if ord(ch) not in DISALLOWED_CODEPOINTS
                   and (unicodedata.category(ch) != "Cc" or ch in "\n\t"))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=10000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          bio TEXT DEFAULT '',
          avatar_color TEXT DEFAULT '#8b5cf6',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          slug TEXT NOT NULL UNIQUE,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title TEXT NOT NULL,
          tagline TEXT NOT NULL,
          description TEXT NOT NULL,
          category TEXT NOT NULL,
          stage TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT '[]',
          tech TEXT NOT NULL DEFAULT '[]',
          repo_url TEXT DEFAULT '',
          demo_url TEXT DEFAULT '',
          website_url TEXT DEFAULT '',
          preview_url TEXT NOT NULL DEFAULT '',
          license TEXT DEFAULT '',
          cover_image TEXT DEFAULT '',
          cover_style TEXT DEFAULT 'violet',
          is_demo INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          views INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS likes (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          PRIMARY KEY(user_id,project_id)
        );
        CREATE TABLE IF NOT EXISTS project_media (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          kind TEXT NOT NULL DEFAULT 'screenshot',
          file TEXT NOT NULL,
          width INTEGER NOT NULL DEFAULT 0,
          height INTEGER NOT NULL DEFAULT 0,
          bytes INTEGER NOT NULL DEFAULT 0,
          animated INTEGER NOT NULL DEFAULT 0,
          position INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
        CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at);
        CREATE INDEX IF NOT EXISTS idx_likes_project ON likes(project_id);
        CREATE INDEX IF NOT EXISTS idx_media_project ON project_media(project_id,position);
        """)
        # CREATE TABLE IF NOT EXISTS never touches an existing table, so a column added after the first
        # release needs its own migration, or an older database answers SELECT p.* without it.
        columns = {row["name"] for row in con.execute("PRAGMA table_info(projects)")}
        if "preview_url" not in columns:
            con.execute("ALTER TABLE projects ADD COLUMN preview_url TEXT NOT NULL DEFAULT ''")
        user_columns = {row["name"] for row in con.execute("PRAGMA table_info(users)")}
        if "google_sub" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
        if "auth_provider" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'password'")
        # A Google subject may only point at one account, while password accounts keep NULL.
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub "
                    "ON users(google_sub) WHERE google_sub IS NOT NULL")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return secrets.compare_digest(derived, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


# Compared against whenever a login names an unknown account, so both paths cost the same.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


ATTEMPT_WINDOW = 600              # seconds
LOGIN_ACCOUNT_LIMIT = 10          # failed password checks against one account
LOGIN_CLIENT_LIMIT = 30           # ...across accounts from one client, i.e. a leaked password list
SIGNUP_CLIENT_LIMIT = 15          # signups from one client; each one costs a scrypt hash
GOOGLE_START_LIMIT = 60           # sign-in redirects from one client; each one mints cookies
_attempts: dict[tuple, deque] = defaultdict(deque)


def attempt_key(kind: str, request: Request, identifier: str) -> tuple:
    client = request.client.host if request.client else "unknown"
    return (kind, client, str(identifier or "").strip().lower()[:120])


def reject_if_spent(key: tuple, limit: int):
    """Refuse early so a stolen credential list cannot be tested against this MVP at speed."""
    bucket = _attempts[key]
    now = time.time()
    while bucket and now - bucket[0] > ATTEMPT_WINDOW:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")


def throttle_login(request: Request, identifier: str):
    reject_if_spent(attempt_key("login", request, identifier), LOGIN_ACCOUNT_LIMIT)
    reject_if_spent(attempt_key("login", request, ""), LOGIN_CLIENT_LIMIT)


def record_attempt(kind: str, request: Request, identifier: str = ""):
    """Charge one expensive try against both buckets; the account-only view would let an
    attacker rotate through a leaked password list untouched."""
    if len(_attempts) > 5000:  # a single process should never hold this many live keys
        _attempts.clear()
    now = time.time()
    for key in {attempt_key(kind, request, identifier), attempt_key(kind, request, "")}:
        _attempts[key].append(now)


def slugify(s: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (slug[:60].strip("-") or "project") + "-" + uuid.uuid4().hex[:6]


def get_field(row, key: str, default=None):
    """Read an optional field from either a sqlite3.Row or a MemoryDB document."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def user_json(r) -> dict:
    return {"id": r["id"], "name": r["name"], "username": r["username"],
            "bio": get_field(r, "bio", ""), "avatar_color": get_field(r, "avatar_color", "#8b5cf6"),
            "email": get_field(r, "email", ""),
            "auth_provider": get_field(r, "auth_provider", "password")}


def safe_next(raw: str) -> str:
    """Keep only a same-origin absolute path; everything else lands on the dashboard.

    A callback URL is attacker-controllable, so an open redirect here would turn
    the sign-in page into a convincing phishing hop.
    """
    target = (raw or "").strip()
    if (not target.startswith("/") or target.startswith("//") or "\\" in target
            or any(ord(ch) < 32 for ch in target)):
        return "/dashboard"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return "/dashboard"
    return target[:300]


def google_display_name(profile: dict) -> str:
    name = clean_text(profile.get("name") or "")
    if len(name) < 2:
        local = profile["email"].split("@")[0].replace(".", " ").replace("_", " ")
        name = clean_text(local).title() or "Univorya Maker"
    return name[:70]


def username_taken(username: str) -> bool:
    if MEMORY:
        return MEMORY.find_user_by_username(username) is not None
    with db() as con:
        return con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone() is not None


def allocate_google_username(profile: dict) -> str:
    """Derive a readable handle from the Google profile, then settle collisions."""
    base = re.sub(r"[^a-z0-9_]+", "", profile["email"].split("@")[0].lower())[:18]
    if len(base) < 3:
        base = re.sub(r"[^a-z0-9_]+", "", (profile.get("name") or "").lower())[:18]
    if len(base) < 3:
        base = "maker"
    for suffix in [""] + [str(n) for n in range(2, 10)]:
        candidate = (base + suffix)[:25]
        if candidate not in RESERVED_USERNAMES and not username_taken(candidate):
            return candidate
    return f"{base[:16]}_{uuid.uuid4().hex[:6]}"


def upsert_google_user(profile: dict):
    """Find, link or create the account behind a verified Google identity.

    Order matters: the stable Google subject wins, then a verified matching email
    links a pre-existing password account, and only a genuinely new identity
    creates a row. Google users get a random unusable password hash so the normal
    password endpoint can never sign them in.
    """
    sub = profile["sub"]
    email = profile["email"]
    name = google_display_name(profile)
    password_hash = hash_password(secrets.token_urlsafe(24))

    if MEMORY:
        user = MEMORY.find_user_by_google_sub(sub) or MEMORY.find_user_by_email(email)
        if user:
            return user if user.get("google_sub") else MEMORY.link_google_account(user, sub)
        for _ in range(3):
            try:
                return MEMORY.create_user(
                    name=name, username=allocate_google_username(profile), email=email,
                    password_hash=password_hash, google_sub=sub, auth_provider="google",
                )
            except DuplicateRecord:
                user = MEMORY.find_user_by_google_sub(sub) or MEMORY.find_user_by_email(email)
                if user:
                    return user
        raise HTTPException(409, "Could not create an account from that Google profile")

    with db() as con:
        user = con.execute("SELECT * FROM users WHERE google_sub=? OR email=? LIMIT 1",
                           (sub, email)).fetchone()
        if user:
            if not user["google_sub"]:
                con.execute("UPDATE users SET google_sub=?, auth_provider='google' WHERE id=?",
                            (sub, user["id"]))
                user = con.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            return user
        for _ in range(3):
            try:
                cur = con.execute(
                    """INSERT INTO users(name,username,email,password_hash,google_sub,auth_provider,created_at)
                       VALUES(?,?,?,?,?,'google',?)""",
                    (name, allocate_google_username(profile), email, password_hash, sub, utcnow()))
                return con.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
            except sqlite3.IntegrityError:
                user = con.execute("SELECT * FROM users WHERE google_sub=? OR email=? LIMIT 1",
                                   (sub, email)).fetchone()
                if user:
                    return user
    raise HTTPException(409, "Could not create an account from that Google profile")


def current_user(request: Request):
    raw = request.cookies.get("univorya_session")
    if not raw:
        return None
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    if MEMORY:
        return MEMORY.user_for_session(token_hash)
    with db() as con:
        return con.execute("""SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
                              WHERE s.token_hash=? AND s.expires > ?""",
                           (token_hash, int(time.time()))).fetchone()


async def keep_media_in_form(request: Request) -> bool:
    """Whether the editor sent keep_media at all, including as an empty list.

    FastAPI turns an empty form value into None, so "keep everything" and "keep nothing"
    would otherwise arrive identically. The parsed form still tells them apart.
    """
    return "keep_media" in await request.form()


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Please sign in to continue")
    return user


def owner_of_project(slug: str):
    """Return the owner id for a showcase project, or None. Studio is a separate
    SQLite store, so it still asks the primary store who owns the listing."""
    if MEMORY:
        row = MEMORY.get_project(slug)
        return row["user_id"] if row else None
    with db() as con:
        row = con.execute("SELECT user_id FROM projects WHERE slug=?", (slug,)).fetchone()
    return row["user_id"] if row else None


def title_of_project(slug: str) -> str:
    if MEMORY:
        row = MEMORY.get_project(slug)
        return row["title"] if row else ""
    with db() as con:
        row = con.execute("SELECT title FROM projects WHERE slug=?", (slug,)).fetchone()
    return row["title"] if row else ""


def require_project_owner(slug: str, request: Request):
    user = require_user(request)
    owner = owner_of_project(slug)
    if owner is None:
        raise HTTPException(404, "Project not found")
    if str(owner) != str(user["id"]):
        raise HTTPException(403, "Only the creator can open this project's Studio")
    return user


def set_session(response: Response, user_id, request: Request):
    token = secrets.token_urlsafe(40)
    lifetime = 60 * 60 * 24 * 30
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if MEMORY:
        MEMORY.create_session(token_hash, user_id, lifetime)
    else:
        with db() as con:
            con.execute("DELETE FROM sessions WHERE expires <= ?", (int(time.time()),))
            con.execute("INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)",
                        (token_hash, user_id, int(time.time()) + lifetime))
    response.set_cookie("univorya_session", token, httponly=True, secure=request.url.scheme == "https",
                        samesite="lax", max_age=lifetime, path="/")


def safe_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or len(raw) > 500:
        raise HTTPException(422, "Links must be valid http(s) URLs without embedded credentials")
    return raw


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def local_preview_url(raw: str) -> str:
    """A loopback address a local script later opens in Chrome. Anything else would turn this
    field into a redirect that runs on the creator's own machine, with no warning page in between."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or len(raw) > 300:
        raise HTTPException(422, "Local preview must be an http(s) URL on this machine")
    if p.hostname.lower() not in LOCAL_HOSTS:
        raise HTTPException(422, "Local preview must point at 127.0.0.1 or localhost")
    return raw


def arr(raw: str, max_count=8) -> list[str]:
    values = [clean_text(x) for x in (raw or "").split(",")]
    seen, unique = set(), []
    for value in values:
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            unique.append(value[:35])
    return unique[:max_count]


def validate_password(password: str):
    """Length alone is theatre: require at least two character classes and no single repeated char."""
    if password != password.strip():
        raise HTTPException(422, "Password can't start or end with spaces")
    classes = sum((bool(re.search(r"[A-Za-z]", password)), bool(re.search(r"\d", password)),
                   bool(re.search(r"[^A-Za-z0-9\s]", password))))
    if classes < 2 or re.fullmatch(r"(.)\1*", password):
        raise HTTPException(422, "Use at least 8 characters that mix letters with digits or symbols")


def validate_project(title: str, tagline: str, description: str, category: str, stage: str):
    if not 3 <= len(title) <= 80:
        raise HTTPException(422, "Project name must be 3–80 characters")
    if not 10 <= len(tagline) <= 140:
        raise HTTPException(422, "Tagline must be 10–140 characters")
    if not 30 <= len(description) <= 5000:
        raise HTTPException(422, "Description must be 30–5000 characters")
    if category not in CATEGORIES or stage not in STAGES:
        raise HTTPException(422, "Choose a valid category and stage")


def media_json(m) -> dict:
    uploaded_at = m.get("uploaded_at") or m.get("created_at", "") if isinstance(m, dict) else m["created_at"]
    return {"id": m["id"], "kind": m["kind"], "file": m["file"], "width": m["width"],
            "height": m["height"], "bytes": m["bytes"], "animated": bool(m["animated"]),
            "uploaded_at": uploaded_at}


def media_for(con, project_ids) -> dict[int, list[dict]]:
    """One query for a whole page of projects, grouped so the client never asks per card."""
    if not project_ids:
        return {}
    marks = ",".join("?" * len(project_ids))
    grouped: dict[int, list[dict]] = {}
    rows = con.execute(f"SELECT * FROM project_media WHERE project_id IN ({marks}) "
                       f"ORDER BY project_id, position, id", list(project_ids))
    for row in rows:
        grouped.setdefault(row["project_id"], []).append(media_json(row))
    return grouped


def project_json(row, viewer_id=None, media: list[dict] | None = None) -> dict:
    result = dict(row)
    result["tags"] = json.loads(result["tags"]) if isinstance(result["tags"], str) else result["tags"]
    result["tech"] = json.loads(result["tech"]) if isinstance(result["tech"], str) else result["tech"]
    result["likes"] = result.pop("like_count", 0)
    result["liked"] = bool(result.pop("viewer_liked", 0))
    result["creator"] = {"name": result.pop("creator_name", ""),
                         "username": result.pop("creator_username", ""),
                         "avatar_color": result.pop("creator_color", "#8b5cf6")}
    result["is_demo"] = bool(result["is_demo"])
    result["is_owner"] = bool(viewer_id and viewer_id == result["user_id"])
    result.pop("version", None)
    if not result["is_owner"]:
        result.pop("preview_url", None)
    result["media"] = media if media is not None else []
    result["media_count"] = len(result["media"])
    return result


PROJECT_SQL = """SELECT p.*, u.name creator_name, u.username creator_username, u.avatar_color creator_color,
     (SELECT COUNT(*) FROM likes l WHERE l.project_id=p.id) like_count,
     (SELECT COUNT(*) FROM likes vl WHERE vl.project_id=p.id AND vl.user_id=?) viewer_liked
     FROM projects p JOIN users u ON p.user_id=u.id"""


class Register(BaseModel):
    name: str = Field(min_length=2, max_length=70)
    username: str = Field(min_length=3, max_length=25)
    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class Login(BaseModel):
    email: str
    password: str


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Browser forms provide Origin. For cookie-authenticated writes it must match this host.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.netloc != request.url.netloc or parsed.scheme != request.url.scheme:
                return JSONResponse(status_code=403, content={"detail": "Cross-origin writes are not allowed"})
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path.startswith("/preview/"):
        # The signed preview route is meant to be framed by the Studio page. The app
        # itself is still protected by X-Frame-Options: DENY below. When Studio and
        # the preview proxy live on different hostnames, set UNIVORYA_APP_ORIGIN so
        # the preview can name that one ancestor instead of blocking every cross-origin
        # frame with SAMEORIGIN.
        resp.headers["Cache-Control"] = "no-store"
        if "content-security-policy" in resp.headers:
            del resp.headers["content-security-policy"]
        app_origin = os.environ.get("UNIVORYA_APP_ORIGIN", "").strip().rstrip("/")
        parsed_app = urlparse(app_origin)
        if parsed_app.scheme in ("http", "https") and parsed_app.netloc:
            resp.headers["Content-Security-Policy"] = (
                f"frame-ancestors 'self' {parsed_app.scheme}://{parsed_app.netloc}")
        else:
            resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # Local Studio previews run on loopback ports. A deployed install should set
    # UNIVORYA_PREVIEW_ORIGIN so only that origin is frameable.
    frame_src = ["'self'", "http://127.0.0.1:*", "http://localhost:*"]
    preview_origin = os.environ.get("UNIVORYA_PREVIEW_ORIGIN", "").strip().rstrip("/")
    if preview_origin:
        parsed = urlparse(preview_origin)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            frame_src.append(f"{parsed.scheme}://{parsed.netloc}")
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "frame-src " + " ".join(frame_src) + "; "
        "form-action 'self'; connect-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'"
    )
    if request.url.path.startswith('/api/'):
        # Every API answer is viewer-specific (likes, ownership) or moves on its own (views).
        resp.headers['Cache-Control'] = 'no-store'
    elif request.url.path.startswith(('/uploads/', '/fonts/')):
        # Both are addressed by a hash of their bytes, so the same URL never changes content.
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    else:
        # Un-hashed app code and every HTML route must revalidate, or a deploy leaves
        # visitors on stale UI.
        resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.post("/api/auth/register", status_code=201)
def register(payload: Register, response: Response, request: Request):
    reject_if_spent(attempt_key("signup", request, ""), SIGNUP_CLIENT_LIMIT)
    email = clean_text(payload.email).strip().lower()
    username = clean_text(payload.username).strip().lower()
    name = clean_text(payload.name)
    if not re.fullmatch(r"[a-z0-9_]{3,25}", username):
        raise HTTPException(422, "Username: 3–25 lowercase letters, numbers or underscores")
    if username in RESERVED_USERNAMES:
        raise HTTPException(422, "That username is reserved for Univorya pages")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Please enter a valid email")
    if not 2 <= len(name) <= 70:
        raise HTTPException(422, "Name must be 2–70 characters")
    validate_password(payload.password)
    # Anything that reaches hashing costs real CPU, whether or not the row is then accepted.
    record_attempt("signup", request)
    password_hash = hash_password(payload.password)
    if MEMORY:
        try:
            user = MEMORY.create_user(
                name=name, username=username, email=email, password_hash=password_hash
            )
        except DuplicateRecord:
            raise HTTPException(409, "Email or username is already registered")
        user_id = user["id"]
    else:
        with db() as con:
            try:
                cur = con.execute("INSERT INTO users(name,username,email,password_hash,created_at) VALUES(?,?,?,?,?)",
                                  (name, username, email, password_hash, utcnow()))
                user_id = cur.lastrowid
                user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Email or username is already registered")
    set_session(response, user_id, request)
    return {"user": user_json(user)}


@app.post("/api/auth/login")
def login(payload: Login, response: Response, request: Request):
    throttle_login(request, payload.email)
    if MEMORY:
        user = MEMORY.find_user_by_identifier(payload.email)
    else:
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE email=? OR username=?",
                               (payload.email.strip().lower(), payload.email.strip().lower())).fetchone()
    # Hashing a throwaway digest for unknown accounts keeps the response time from naming valid ones.
    digest = user["password_hash"] if user else DUMMY_HASH
    if not verify_password(payload.password, digest):
        record_attempt("login", request, payload.email)
        raise HTTPException(401, "Incorrect email/username or password. "
                                 "If you signed up with Google, use Continue with Google.")
    set_session(response, user["id"], request)
    return {"user": user_json(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    raw = request.cookies.get("univorya_session")
    if raw:
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        if MEMORY:
            MEMORY.delete_session(token_hash)
        else:
            with db() as con:
                con.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
    response.delete_cookie("univorya_session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"user": user_json(user) if user else None}


def clear_oauth_cookies(response: Response):
    for name in ("univorya_oauth_state", "univorya_oauth_verifier", "univorya_oauth_next"):
        response.delete_cookie(name, path="/auth/google")


def google_failure(reason: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?google_error={reason}", status_code=302)
    clear_oauth_cookies(response)
    return response


@app.get("/auth/google/start")
def google_start(request: Request):
    """Begin the authorization-code flow with state and PKCE in HttpOnly cookies."""
    reject_if_spent(attempt_key("google-start", request, ""), GOOGLE_START_LIMIT)
    cfg = google_oauth.config(str(request.base_url).rstrip("/") + "/auth/google/callback")
    if not cfg:
        raise HTTPException(503, "Google sign-in is not configured")
    record_attempt("google-start", request)
    state = secrets.token_urlsafe(32)
    verifier, challenge = google_oauth.new_pkce()
    response = RedirectResponse(google_oauth.authorization_url(cfg, state, challenge), status_code=302)
    secure = request.url.scheme == "https"
    for name, value in (
        ("univorya_oauth_state", state),
        ("univorya_oauth_verifier", verifier),
        ("univorya_oauth_next", safe_next(request.query_params.get("next", ""))),
    ):
        response.set_cookie(name, value, max_age=600, httponly=True, secure=secure,
                            samesite="lax", path="/auth/google")
    return response


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Verify the callback, then sign in, link or create the account behind it."""
    cfg = google_oauth.config(str(request.base_url).rstrip("/") + "/auth/google/callback")
    if not cfg:
        raise HTTPException(503, "Google sign-in is not configured")
    expected = request.cookies.get("univorya_oauth_state", "")
    verifier = request.cookies.get("univorya_oauth_verifier", "")
    if (not state or not expected or not verifier
            or not secrets.compare_digest(state.encode("utf-8", "ignore"), expected.encode("utf-8"))):
        return google_failure("state")
    if error:
        return google_failure("denied" if error == "access_denied" else "provider")
    if not code:
        return google_failure("missing_code")
    try:
        access_token = google_oauth.exchange_code(cfg, code, verifier)
        profile = google_oauth.fetch_profile(cfg, access_token)
        user = upsert_google_user(profile)
    except google_oauth.GoogleOAuthError:
        return google_failure("exchange")
    except HTTPException:
        return google_failure("account")
    target = safe_next(request.cookies.get("univorya_oauth_next", ""))
    response = RedirectResponse(target, status_code=302)
    set_session(response, user["id"], request)
    clear_oauth_cookies(response)
    return response


@app.get("/api/metadata")
def metadata():
    if MEMORY:
        counts = MEMORY.community_counts()
        makers, projects = counts["makers"], counts["projects"]
    else:
        with db() as con:
            makers = con.execute("SELECT COUNT(*) FROM users WHERE id NOT IN (SELECT user_id FROM projects WHERE is_demo=1)").fetchone()[0]
            projects = con.execute("SELECT COUNT(*) FROM projects WHERE is_demo=0").fetchone()[0]
    return {"categories": CATEGORIES, "stages": STAGES, "cover_styles": list(COVER_STYLES),
            "google": {"enabled": google_oauth.configured()},
            "limits": {"max_file_mb": MAX_FILE_BYTES // (1024 * 1024), "max_total_mb": MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024),
                       "max_media": MAX_MEDIA_PER_PROJECT, "min_width": MIN_IMAGE_WIDTH, "min_height": MIN_IMAGE_HEIGHT},
            "studio": {"enabled": os.environ.get("UNIVORYA_STUDIO", "1") != "0",
                       "runner": "local",
                       "preview_origin": os.environ.get("UNIVORYA_PREVIEW_ORIGIN", "").strip(),
                       "providers": studio.provider_catalog(), "templates": studio.TEMPLATES},
            "community": {"makers": makers, "projects": projects}}


@app.get("/api/projects")
def list_projects(request: Request, q: str = "", category: str = "All", stage: str = "All", sort: str = "trending",
                  limit: int = 24, page: int = 1, creator: str = ""):
    if not 1 <= limit <= 60 or page < 1:
        raise HTTPException(422, "Invalid pagination")
    viewer = current_user(request)
    if category != "All" and category not in CATEGORIES:
        raise HTTPException(422, "Invalid category")
    if stage != "All" and stage not in STAGES:
        raise HTTPException(422, "Invalid stage")
    if sort not in ("trending", "newest", "popular"):
        raise HTTPException(422, "Invalid sort")
    if MEMORY:
        rows, total = MEMORY.list_projects(
            q=q, category=category, stage=stage, creator=creator, sort=sort,
            limit=limit, page=page, viewer_id=viewer["id"] if viewer else None,
        )
        return {"projects": [project_json(row, viewer["id"] if viewer else None, row.get("media", []))
                             for row in rows],
                "pagination": {"page": page, "limit": limit, "total": total,
                               "pages": (total + limit - 1) // limit}}
    clauses, params = [], []
    if q.strip():
        term = "%" + q.strip()[:100].replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(p.title LIKE ? ESCAPE '\\' OR p.tagline LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\' OR p.tags LIKE ? ESCAPE '\\')")
        params += [term] * 4
    if category != "All":
        if category not in CATEGORIES:
            raise HTTPException(422, "Invalid category")
        clauses.append("p.category=?"); params.append(category)
    if stage != "All":
        if stage not in STAGES:
            raise HTTPException(422, "Invalid stage")
        clauses.append("p.stage=?"); params.append(stage)
    if creator:
        clauses.append("u.username=?"); params.append(creator)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    order = {"trending": "like_count DESC, p.views DESC, p.created_at DESC",
             "newest": "p.created_at DESC", "popular": "p.views DESC, like_count DESC"}.get(sort)
    if not order:
        raise HTTPException(422, "Invalid sort")
    with db() as con:
        total = con.execute("SELECT COUNT(*) FROM projects p JOIN users u ON u.id=p.user_id" + where, params).fetchone()[0]
        rows = con.execute(PROJECT_SQL + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                           [viewer["id"] if viewer else -1] + params + [limit, (page-1)*limit]).fetchall()
        grouped = media_for(con, [row["id"] for row in rows])
    return {"projects": [project_json(row, viewer["id"] if viewer else None, grouped.get(row["id"], []))
                         for row in rows],
            "pagination": {"page": page, "limit": limit, "total": total, "pages": (total + limit - 1) // limit}}


@app.get("/api/projects/{slug}")
def project_detail(slug: str, request: Request):
    viewer = current_user(request)
    if MEMORY:
        row = MEMORY.get_project(slug, viewer["id"] if viewer else None, increment_views=True)
        if not row:
            raise HTTPException(404, "Project not found")
        return {"project": project_json(row, viewer["id"] if viewer else None, row.get("media", []))}
    with db() as con:
        row = con.execute(PROJECT_SQL + " WHERE p.slug=?", (viewer["id"] if viewer else -1, slug)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        con.execute("UPDATE projects SET views=views+1 WHERE id=?", (row["id"],))
        grouped = media_for(con, [row["id"]])
    result = project_json(row, viewer["id"] if viewer else None, grouped.get(row["id"], []))
    result["views"] += 1
    return {"project": result}


@app.get("/api/makers/{username}")
def maker_profile(username: str, request: Request):
    """A real profile or a real 404 — /makers/anything must not invent a person."""
    if MEMORY:
        maker = MEMORY.maker_stats(username)
        if not maker:
            raise HTTPException(404, "No creator with that username")
        return {"maker": maker}
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        if not user:
            raise HTTPException(404, "No creator with that username")
        stats = con.execute("""SELECT COUNT(*) projects, COALESCE(SUM(views),0) views FROM projects
                               WHERE user_id=?""", (user["id"],)).fetchone()
        likes = con.execute("""SELECT COUNT(*) FROM likes l JOIN projects p ON p.id=l.project_id
                               WHERE p.user_id=?""", (user["id"],)).fetchone()[0]
    return {"maker": {**user_json(user), "joined_at": user["created_at"], "project_count": stats["projects"],
                      "views": stats["views"], "likes": likes}}


def read_upload(cover: UploadFile | None) -> bytes:
    if not cover or not cover.filename:
        return b""
    raw = cover.file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, "Image must be under 5 MB")
    if not raw:
        # A picked file that arrives empty must not be dropped in silence.
        raise HTTPException(422, f"“{clean_text(cover.filename)[:60]}” is empty")
    return raw


def save_image(raw: bytes) -> dict:
    """Decode an allowed image, shrink it (never upscale), re-encode as WebP keeping animation."""
    try:
        im = Image.open(io.BytesIO(raw))
        if im.format not in ALLOWED_IMAGE_FORMATS:
            raise HTTPException(422, "Use PNG, JPG, WEBP or GIF images")
        if im.width * im.height > 22_000_000:
            raise HTTPException(422, "Image dimensions are too large")
        if im.width < MIN_IMAGE_WIDTH or im.height < MIN_IMAGE_HEIGHT:
            raise HTTPException(422, f"Image must be at least {MIN_IMAGE_WIDTH} × {MIN_IMAGE_HEIGHT} pixels "
                                     f"so it does not blur in the gallery")
        frame_count = getattr(im, "n_frames", 1)
        animate = 1 < frame_count <= MAX_ANIMATED_FRAMES
        frames, durations, size, decoded = [], [], None, 0
        for index in range(frame_count if animate else 1):
            im.seek(index)
            decoded += im.width * im.height
            if decoded > MAX_ANIMATION_PIXELS:
                raise HTTPException(422, "That animation has too many pixels to process safely")
            frame = ImageOps.exif_transpose(im).convert("RGB")
            if size is None:
                frame.thumbnail((1600, 1000), Image.Resampling.LANCZOS)
                size = frame.size
            elif frame.size != size:
                frame = frame.resize(size, Image.Resampling.LANCZOS)
            frames.append(frame)
            durations.append(max(int(im.info.get("duration", 100)), 20))
        filename = f"{uuid.uuid4().hex}.webp"
        target = UPLOAD_DIR / filename
        options = {"format": "WEBP", "quality": 84, "method": 5}
        if len(frames) > 1:
            options.update(save_all=True, append_images=frames[1:], duration=durations, loop=0)
        frames[0].save(target, **options)
        return {"file": f"/uploads/{filename}", "width": size[0], "height": size[1],
                "bytes": target.stat().st_size, "animated": len(frames) > 1}
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "Image file is invalid or too large")


def store_upload(con, raw: bytes, project_id: int, kind: str) -> dict:
    """Encode one accepted image and record it as a queryable row on the caller's transaction."""
    meta = save_image(raw)
    position = con.execute("SELECT COUNT(*) FROM project_media WHERE project_id=? AND kind='screenshot'",
                           (project_id,)).fetchone()[0] if kind == "screenshot" else 0
    cur = con.execute("""INSERT INTO project_media(project_id,kind,file,width,height,bytes,animated,position,
                         created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                      (project_id, kind, meta["file"], meta["width"], meta["height"], meta["bytes"],
                       int(meta["animated"]), position, utcnow()))
    meta.update(id=cur.lastrowid, kind=kind, position=position)
    return meta


def remove_file(file: str):
    if file:
        (UPLOAD_DIR / Path(file).name).unlink(missing_ok=True)


def read_uploads(cover: UploadFile | None, screenshots) -> list[tuple[str, bytes]]:
    """Read every picked file up front and enforce one combined budget, not just a per-file one."""
    picked = [item for item in (screenshots or []) if item and item.filename]
    if len(picked) > MAX_MEDIA_PER_PROJECT:
        raise HTTPException(422, f"Add up to {MAX_MEDIA_PER_PROJECT} screenshots per project")
    blobs = [("cover", raw) for raw in [read_upload(cover)] if raw]
    blobs += [("screenshot", read_upload(item)) for item in picked]
    if sum(len(raw) for _, raw in blobs) > MAX_TOTAL_UPLOAD_BYTES:
        raise HTTPException(413, "The selected images add up to more than 25 MB")
    return blobs


def update_project_in_memory(slug: str, user, blobs, keep_values, fields: dict):
    """Apply an owner edit to one MemoryDB project, files first and rows after.

    The project document carries its media metadata, so a single versioned PATCH
    replaces the gallery as well as the text.  Files are written before the PATCH and
    removed again if it fails, which keeps the disk and the document in step.
    """
    row = MEMORY.get_project(slug)
    if not row:
        raise HTTPException(404, "Project not found")
    if row["user_id"] != user["id"] or row.get("is_demo"):
        raise HTTPException(403, "Only the creator can edit this project")

    has_new_cover = any(kind == "cover" for kind, _ in blobs)
    kept, stale = [], []
    for item in row.get("media") or []:
        drop = (item["kind"] == "cover" and has_new_cover) or (
            item["kind"] == "screenshot" and keep_values is not None
            and str(item["id"]) not in keep_values
        )
        if drop:
            stale.append(item["file"])
        else:
            kept.append(dict(item))

    written = []
    cover_image = row.get("cover_image", "")
    shot_position = sum(1 for item in kept if item["kind"] == "screenshot")
    try:
        for kind, raw in blobs:
            meta = save_image(raw)
            item = {
                "id": uuid.uuid4().hex,
                "kind": kind,
                "file": meta["file"],
                "width": meta["width"],
                "height": meta["height"],
                "bytes": meta["bytes"],
                "animated": bool(meta["animated"]),
                "position": 0 if kind == "cover" else shot_position,
                "uploaded_at": utcnow(),
            }
            if kind == "screenshot":
                shot_position += 1
            kept.append(item)
            written.append(meta["file"])
            if kind == "cover":
                cover_image = meta["file"]
        position = 0
        for item in kept:
            if item["kind"] == "screenshot":
                item["position"] = position
                position += 1
            else:
                item["position"] = 0
        fields = {**fields, "cover_image": cover_image}
        MEMORY.update_project(row, fields, kept)
    except Exception:
        for file in written:
            remove_file(file)
        raise
    for file in stale:
        remove_file(file)
    return {"slug": slug, "url": f"/p/{slug}"}


@app.post("/api/projects", status_code=201)
def create_project(user: sqlite3.Row = Depends(require_user),
                   title: str = Form(...), tagline: str = Form(...),
                   description: str = Form(...), category: str = Form(...), stage: str = Form(...),
                   tags: str = Form(""), tech: str = Form(""), repo_url: str = Form(""),
                   demo_url: str = Form(""), website_url: str = Form(""), license: str = Form(""),
                   preview_url: str = Form(""),
                   cover_style: str = Form("violet"), cover: UploadFile | None = File(None),
                   screenshots: list[UploadFile] | None = File(None)):
    """Publish a project and every image that came with it in one transaction."""
    title, tagline, description, license = map(clean_text, (title, tagline, description, license))
    validate_project(title, tagline, description, category, stage)
    repo_url, demo_url, website_url = map(safe_url, [repo_url, demo_url, website_url])
    preview_url = local_preview_url(preview_url)
    if len(license) > 70:
        raise HTTPException(422, "License name is too long")
    if cover_style not in COVER_STYLES:
        raise HTTPException(422, "Invalid cover style")
    blobs = read_uploads(cover, screenshots)
    now = utcnow()
    slug = slugify(title)
    written: list[str] = []
    if MEMORY:
        media, cover_image, shot_position = [], "", 0
        try:
            for kind, raw in blobs:
                meta = save_image(raw)
                item = {
                    "id": uuid.uuid4().hex,
                    "kind": kind,
                    "file": meta["file"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "bytes": meta["bytes"],
                    "animated": bool(meta["animated"]),
                    "position": 0 if kind == "cover" else shot_position,
                    "uploaded_at": now,
                }
                if kind == "screenshot":
                    shot_position += 1
                media.append(item)
                written.append(meta["file"])
                if kind == "cover":
                    cover_image = meta["file"]
            MEMORY.create_project({
                "slug": slug, "user_id": user["id"], "title": title, "tagline": tagline,
                "description": description, "category": category, "stage": stage,
                "tags": arr(tags), "tech": arr(tech), "repo_url": repo_url,
                "demo_url": demo_url, "website_url": website_url,
                "preview_url": preview_url, "license": license,
                "cover_image": cover_image, "cover_style": cover_style,
                "creator_name": user["name"], "creator_username": user["username"],
                "creator_color": user["avatar_color"],
            }, media)
        except Exception:
            for file in written:
                remove_file(file)
            raise
        return {"slug": slug, "url": f"/p/{slug}"}
    try:
        with db() as con:
            project_id = con.execute("""INSERT INTO projects(slug,user_id,title,tagline,description,category,stage,
                tags,tech,repo_url,demo_url,website_url,preview_url,license,cover_image,cover_style,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (slug, user["id"], title, tagline, description, category, stage,
                 json.dumps(arr(tags), ensure_ascii=False), json.dumps(arr(tech), ensure_ascii=False), repo_url, demo_url, website_url, preview_url, license,
                 "", cover_style, now, now)).lastrowid
            cover_image = ""
            for kind, raw in blobs:
                meta = store_upload(con, raw, project_id, kind)
                written.append(meta["file"])
                if kind == "cover":
                    cover_image = meta["file"]
            if cover_image:
                con.execute("UPDATE projects SET cover_image=? WHERE id=?", (cover_image, project_id))
    except Exception:
        for file in written:
            remove_file(file)
        raise
    return {"slug": slug, "url": f"/p/{slug}"}


@app.put("/api/projects/{slug}")
def update_project(slug: str, user: sqlite3.Row = Depends(require_user),
                   keep_media_sent: bool = Depends(keep_media_in_form),
                   title: str = Form(...), tagline: str = Form(...),
                   description: str = Form(...), category: str = Form(...), stage: str = Form(...),
                   tags: str = Form(""), tech: str = Form(""), repo_url: str = Form(""),
                   demo_url: str = Form(""), website_url: str = Form(""), license: str = Form(""),
                   preview_url: str = Form(""),
                   cover_style: str = Form("violet"), cover: UploadFile | None = File(None),
                   screenshots: list[UploadFile] | None = File(None), keep_media: str | None = Form(None)):
    """Edit only the caller's project; preserve slug and older cover unless replaced.

    keep_media is the list of screenshot ids the editor wants retained. Omitting the field
    leaves existing screenshots untouched, so a plain text edit never wipes the gallery;
    sending it empty means the editor removed every one of them.
    """
    title, tagline, description, license = map(clean_text, (title, tagline, description, license))
    validate_project(title, tagline, description, category, stage)
    repo_url, demo_url, website_url = map(safe_url, [repo_url, demo_url, website_url])
    preview_url = local_preview_url(preview_url)
    if len(license) > 70:
        raise HTTPException(422, "License name is too long")
    if cover_style not in COVER_STYLES:
        raise HTTPException(422, "Invalid cover style")
    blobs = read_uploads(cover, screenshots)
    keep_values = None if not keep_media_sent else {
        value.strip() for value in (keep_media or "").split(",") if value.strip()}
    if MEMORY:
        return update_project_in_memory(slug, user, blobs, keep_values, {
            "title": title, "tagline": tagline, "description": description,
            "category": category, "stage": stage, "tags": arr(tags), "tech": arr(tech),
            "repo_url": repo_url, "demo_url": demo_url, "website_url": website_url,
            "preview_url": preview_url, "license": license, "cover_style": cover_style,
        })
    written: list[str] = []
    stale: list[str] = []
    try:
        with db() as con:
            # Re-check ownership inside the write transaction to avoid a stale-read race.
            row = con.execute("SELECT * FROM projects WHERE slug=? AND user_id=? AND is_demo=0",
                              (slug, user["id"])).fetchone()
            if not row:
                if con.execute("SELECT 1 FROM projects WHERE slug=?", (slug,)).fetchone():
                    raise HTTPException(403, "Only the creator can edit this project")
                raise HTTPException(404, "Project not found")
            media = con.execute("SELECT id,kind,file FROM project_media WHERE project_id=? ORDER BY position,id",
                                 (row["id"],)).fetchall()
            for item in media:
                drop = (item["kind"] == "cover" and any(kind == "cover" for kind, _ in blobs)) or \
                        (item["kind"] == "screenshot" and keep_values is not None
                         and str(item["id"]) not in keep_values)
                if drop:
                    stale.append(item["file"])
                    con.execute("DELETE FROM project_media WHERE id=?", (item["id"],))
            cover_image = row["cover_image"]
            for kind, raw in blobs:
                meta = store_upload(con, raw, row["id"], kind)
                written.append(meta["file"])
                if kind == "cover":
                    cover_image = meta["file"]
            for position, item in enumerate(con.execute(
                    "SELECT id FROM project_media WHERE project_id=? AND kind='screenshot' ORDER BY position,id",
                    (row["id"],))):
                con.execute("UPDATE project_media SET position=? WHERE id=?", (position, item["id"]))
            con.execute("""UPDATE projects SET title=?,tagline=?,description=?,category=?,stage=?,tags=?,tech=?,
                repo_url=?,demo_url=?,website_url=?,preview_url=?,license=?,cover_image=?,cover_style=?,updated_at=?
                WHERE id=?""",
                (title, tagline, description, category, stage,
                 json.dumps(arr(tags), ensure_ascii=False), json.dumps(arr(tech), ensure_ascii=False), repo_url, demo_url, website_url, preview_url, license,
                 cover_image, cover_style, utcnow(), row["id"]))
    except Exception:
        for file in written:
            remove_file(file)
        raise
    for file in stale:
        remove_file(file)
    return {"slug": slug, "url": f"/p/{slug}"}


@app.delete("/api/projects/{slug}")
def delete_project(slug: str, request: Request):
    user = require_user(request)
    if MEMORY:
        row = MEMORY.get_project(slug)
        if not row:
            raise HTTPException(404, "Project not found")
        if row["user_id"] != user["id"]:
            raise HTTPException(403, "Only the creator can delete this project")
        files = [item["file"] for item in row.get("media") or []]
        MEMORY.delete_project(row)
        if row.get("cover_image") and row["cover_image"] not in files:
            files.append(row["cover_image"])
        for file in files:
            remove_file(file)
        return {"ok": True}
    with db() as con:
        row = con.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        if row["user_id"] != user["id"]:
            raise HTTPException(403, "Only the creator can delete this project")
        files = [item["file"] for item in con.execute(
            "SELECT file FROM project_media WHERE project_id=?", (row["id"],))]
        con.execute("DELETE FROM projects WHERE id=?", (row["id"],))
    if row["cover_image"] and row["cover_image"] not in files:
        files.append(row["cover_image"])
    for file in files:
        remove_file(file)
    return {"ok": True}


@app.post("/api/projects/{slug}/like")
def like_project(slug: str, request: Request):
    user = require_user(request)
    if MEMORY:
        try:
            liked, count = MEMORY.toggle_like(slug, user["id"])
        except RecordNotFound:
            raise HTTPException(404, "Project not found")
        return {"liked": liked, "likes": count}
    with db() as con:
        row = con.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        found = con.execute("SELECT 1 FROM likes WHERE user_id=? AND project_id=?", (user["id"], row["id"])).fetchone()
        if found:
            con.execute("DELETE FROM likes WHERE user_id=? AND project_id=?", (user["id"], row["id"]))
        else:
            con.execute("INSERT INTO likes(user_id,project_id) VALUES(?,?)", (user["id"], row["id"]))
        count = con.execute("SELECT COUNT(*) FROM likes WHERE project_id=?", (row["id"],)).fetchone()[0]
    return {"liked": not bool(found), "likes": count}


@app.get("/api/health")
def health():
    if MEMORY:
        try:
            MEMORY.health()
        except MemoryDBError:
            return JSONResponse(status_code=503,
                                content={"status": "degraded", "service": "univorya", "store": "memorydb"})
        return {"status": "ok", "service": "univorya", "store": "memorydb"}
    return {"status": "ok", "service": "univorya", "store": "sqlite"}


# ------------------------------------------------------------------ Studio API
# Studio is intentionally small: the owner creates a workspace for one of their
# projects, connects their own model key, chats with the coding agent, and previews
# the running app in a split pane. All state lives in DATA_DIR/studio/studio.sqlite3.

class StudioWorkspaceBody(BaseModel):
    template: str = "static"


class StudioChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=studio.MAX_MESSAGE_CHARS)


class StudioCredentialBody(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""


class StudioPreviewBody(BaseModel):
    command: str = ""


class StudioGitCredentialBody(BaseModel):
    token: str
    username: str = "x-access-token"


class StudioGitCloneBody(BaseModel):
    url: str
    branch: str = ""
    replace: bool = False


class StudioGitCommitBody(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    paths: list[str] = []


def studio_workspace_json(row: dict | None) -> dict | None:
    if not row:
        return None
    return {"slug": row["slug"], "template": row["template"], "status": row["status"],
            "port": int(row.get("port") or 0), "command": row.get("command") or "",
            "git_remote": row.get("git_remote") or "", "git_branch": row.get("git_branch") or "",
            "created_at": row.get("created_at", ""), "updated_at": row.get("updated_at", "")}


STOPPED_PREVIEW = {"status": "stopped", "running": False, "url": "", "port": 0,
                   "command": "", "error": ""}


@app.get("/api/studio/projects/{slug}")
def studio_project(slug: str, request: Request):
    user = require_project_owner(slug, request)
    row = studio.get_workspace(slug)
    if row:
        files = studio.workspace_files(slug, user["id"])
        messages = studio.messages_for(slug, user["id"])
        preview = studio.preview_status(slug, user["id"])
    else:
        files, messages, preview = [], [], dict(STOPPED_PREVIEW)
    return {"workspace": studio_workspace_json(row), "credential": studio.credential_public(user["id"]),
            "files": files, "messages": messages, "preview": preview,
            "git": studio.git_status(slug, user["id"]) if row else None,
            "git_credential": studio.git_credential_public(user["id"]),
            "git_available": studio.git_available(),
            "project": {"slug": slug, "title": title_of_project(slug)}}


@app.put("/api/studio/projects/{slug}/workspace")
def studio_create_workspace(slug: str, payload: StudioWorkspaceBody, request: Request):
    user = require_project_owner(slug, request)
    row = studio.ensure_workspace(slug, user["id"], payload.template)
    return {"workspace": studio_workspace_json(row), "files": studio.workspace_files(slug, user["id"]),
            "preview": studio.preview_status(slug, user["id"]),
            "git": studio.git_status(slug, user["id"])}


@app.get("/api/studio/projects/{slug}/files")
def studio_files(slug: str, request: Request):
    user = require_project_owner(slug, request)
    return {"files": studio.workspace_files(slug, user["id"]),
            "tree": studio.project_tree(slug, user["id"])}


@app.get("/api/studio/projects/{slug}/messages")
def studio_messages(slug: str, request: Request):
    user = require_project_owner(slug, request)
    return {"messages": studio.messages_for(slug, user["id"])}


@app.post("/api/studio/projects/{slug}/chat")
def studio_chat(slug: str, payload: StudioChatBody, request: Request):
    user = require_project_owner(slug, request)
    result = studio.chat(slug, user["id"], payload.message)
    result["files"] = studio.workspace_files(slug, user["id"])
    result["git"] = studio.git_status(slug, user["id"])
    return result


@app.get("/api/studio/credential")
def studio_credential(request: Request):
    user = require_user(request)
    return {"credential": studio.credential_public(user["id"])}


@app.put("/api/studio/credential")
def studio_save_credential(payload: StudioCredentialBody, request: Request):
    user = require_user(request)
    credential = studio.save_credential(user["id"], payload.provider, payload.model,
                                        payload.api_key, payload.base_url)
    return {"credential": credential}


@app.delete("/api/studio/credential")
def studio_delete_credential(request: Request):
    user = require_user(request)
    studio.delete_credential(user["id"])
    return {"credential": None}


@app.get("/api/studio/git/credential")
def studio_git_credential(request: Request):
    user = require_user(request)
    return {"credential": studio.git_credential_public(user["id"])}


@app.put("/api/studio/git/credential")
def studio_save_git_credential(payload: StudioGitCredentialBody, request: Request):
    user = require_user(request)
    return {"credential": studio.save_git_credential(user["id"], payload.token, payload.username)}


@app.delete("/api/studio/git/credential")
def studio_delete_git_credential(request: Request):
    user = require_user(request)
    studio.delete_git_credential(user["id"])
    return {"credential": None}


@app.get("/api/studio/projects/{slug}/git")
def studio_git(slug: str, request: Request):
    user = require_project_owner(slug, request)
    return {"git": studio.git_status(slug, user["id"]),
            "credential": studio.git_credential_public(user["id"]),
            "available": studio.git_available()}


@app.post("/api/studio/projects/{slug}/git/clone")
def studio_git_clone(slug: str, payload: StudioGitCloneBody, request: Request):
    user = require_project_owner(slug, request)
    result = studio.git_clone(slug, user["id"], payload.url, payload.branch, payload.replace)
    return {"git": result, "files": studio.workspace_files(slug, user["id"]),
            "preview": studio.preview_status(slug, user["id"])}


@app.get("/api/studio/projects/{slug}/git/diff")
def studio_git_diff(slug: str, request: Request, staged: bool = False):
    user = require_project_owner(slug, request)
    return {"diff": studio.git_diff(slug, user["id"], staged)}


@app.post("/api/studio/projects/{slug}/git/commit")
def studio_git_commit(slug: str, payload: StudioGitCommitBody, request: Request):
    user = require_project_owner(slug, request)
    return {"git": studio.git_commit(slug, user["id"], payload.message, payload.paths)}


@app.post("/api/studio/projects/{slug}/git/push")
def studio_git_push(slug: str, request: Request):
    user = require_project_owner(slug, request)
    return {"git": studio.git_push(slug, user["id"])}


@app.post("/api/studio/projects/{slug}/git/pull")
def studio_git_pull(slug: str, request: Request):
    user = require_project_owner(slug, request)
    return {"git": studio.git_pull(slug, user["id"]),
            "files": studio.workspace_files(slug, user["id"])}


@app.post("/api/studio/projects/{slug}/preview/start")
def studio_start(slug: str, payload: StudioPreviewBody, request: Request):
    user = require_project_owner(slug, request)
    return {"preview": studio.start_preview(slug, user["id"], payload.command)}


@app.post("/api/studio/projects/{slug}/preview/stop")
def studio_stop(slug: str, request: Request):
    user = require_project_owner(slug, request)
    return {"preview": studio.stop_preview(slug, user["id"])}


@app.get("/api/studio/projects/{slug}/preview")
def studio_preview(slug: str, request: Request):
    user = require_project_owner(slug, request)
    return {"preview": studio.preview_status(slug, user["id"])}


@app.get("/api/studio/projects/{slug}/preview/log")
def studio_preview_log(slug: str, request: Request, lines: int = 80):
    user = require_project_owner(slug, request)
    return {"log": studio.preview_log(slug, user["id"], lines)}


async def _proxy_preview(token: str, resource: str, request: Request):
    verified = studio.verify_preview_token(token)
    if not verified:
        raise HTTPException(404, "Preview not found")
    slug, user_id = verified
    port = studio.preview_target(slug, user_id)
    query = ("?" + request.url.query) if request.url.query else ""
    target = f"http://127.0.0.1:{port}/{resource}{query}"
    body = await request.body()
    headers = {}
    for name in ("accept", "accept-language", "content-type", "range",
                 "if-none-match", "if-modified-since", "user-agent"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False)
    try:
        upstream = await client.send(client.build_request(request.method, target,
                                                          headers=headers, content=body), stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(502, "The preview app did not answer")

    out_headers = {}
    for name in ("content-type", "content-disposition", "content-language",
                 "etag", "last-modified", "content-encoding"):
        value = upstream.headers.get(name)
        if value:
            out_headers[name] = value
    location = upstream.headers.get("location")
    if location:
        out_headers["location"] = studio.rewrite_location(location, token)
    content_type = (upstream.headers.get("content-type") or "").lower()
    # HTML and CSS are rewritten so root-absolute asset URLs keep working under
    # /preview/<token>/. Everything else is relayed byte-for-byte.
    if request.method != "HEAD" and ("text/html" in content_type or "text/css" in content_type):
        raw = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        charset = "utf-8"
        match = re.search(r"charset=([\w.-]+)", content_type)
        if match:
            charset = match.group(1)
        text = raw.decode(charset, errors="replace")
        prefix = f"preview/{token}"
        text = (studio.rewrite_preview_html(text, prefix) if "text/html" in content_type
                else studio.rewrite_preview_css(text, prefix))
        out_headers.pop("content-encoding", None)
        return Response(content=text.encode("utf-8"), status_code=upstream.status_code,
                        headers=out_headers)

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(relay(), status_code=upstream.status_code, headers=out_headers)


@app.api_route("/preview/{token}/", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
               include_in_schema=False)
async def preview_root(token: str, request: Request):
    return await _proxy_preview(token, "", request)


@app.api_route("/preview/{token}/{resource:path}",
               methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
               include_in_schema=False)
async def preview_resource(token: str, resource: str, request: Request):
    return await _proxy_preview(token, resource, request)


# Fictional demo data keep the gallery populated on first boot. Public API identifies these as demos.
DEMO = [
    ("Untitled UI", "A beautiful canvas for designers to think out loud.", "Design", "Launched", "Figma, React, Design System", "violet", "A minimal, collaborative design playground for makers who love to experiment with visual systems, components and motion. A fictional example to illustrate a project showcase.", "Lena Torres"),
    ("Orbit AI", "Your own intelligent workspace, without the clutter.", "AI & ML", "Beta", "Python, FastAPI, LLM", "blue", "An AI-powered research companion that helps organize documents and turn scattered ideas into actionable insights. A fictional demo project for illustrating the platform.", "Alex Morgan"),
    ("Codewave", "The developer toolkit that moves at your speed.", "Dev Tool", "Launched", "TypeScript, CLI, Open Source", "mint", "A modular command-line development toolkit focused on keeping projects productive and bringing tasks into one place. This is a fictional sample project.", "Sam Park"),
    ("Mellow", "Mindful moments, one beautiful screen at a time.", "Mobile App", "Building", "Flutter, Mobile, Wellness", "pink", "A simple mobile interface for reflection and daily routines, designed with a calming and distraction-free user experience. This is a fictional showcase example.", "Riya Patel"),
    ("Pixel Pantry", "A little kitchen for your creative ideas.", "Web App", "Beta", "Next.js, Supabase, UI", "orange", "A playful space for saving creative snippets, inspiration boards and tiny experiments you want to revisit later. Fictional project used to populate this preview.", "Noah Reed"),
    ("OpenTrail", "Adventures are better when the map is yours.", "Open Source", "Launched", "React, Maps, Open Source", "slate", "A community-friendly travel planning concept with maps, notes and collaborative routes built for curious explorers. Fictional example for demo purposes.", "Jamie Quinn"),
    ("Synthflow", "Build sounds and experiences that feel alive.", "AI & ML", "Idea", "Audio, AI, Python", "blue", "An experimental audio creation workspace with playful AI-driven prototyping and collaboration features. Fictional project shown as an early-stage idea.", "Lena Torres"),
    ("Framerly", "Launch your next landing page with delight.", "Web App", "Launched", "HTML, CSS, JavaScript", "violet", "A modern landing-page kit with polished components and responsive layouts for builders who want to ship faster. Fictional showcase project.", "Sam Park"),
]


def seed_demo():
    if os.environ.get("UNIVORYA_SEED_DEMO", "1") == "0":
        return
    if MEMORY:
        MEMORY.seed_demo(DEMO, slugify)
        return
    with db() as con:
        if con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]:
            return
        for idx, (title, tagline, category, stage, tech, color, description, name) in enumerate(DEMO):
            username = "demo_" + re.sub(r"[^a-z]+", "", name.lower())
            user = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if user:
                uid = user["id"]
            else:
                uid = con.execute("INSERT INTO users(name,username,email,password_hash,created_at,avatar_color) VALUES(?,?,?,?,?,?)",
                                  (name, username, f"{username}@example.invalid", "disabled-demo-account", utcnow(),
                                   ["#8b5cf6", "#3b82f6", "#14b8a6", "#f97316"][idx % 4])).lastrowid
            con.execute("""INSERT INTO projects(slug,user_id,title,tagline,description,category,stage,tags,tech,
                       cover_style,is_demo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (slugify(title), uid, title, tagline, description, category, stage,
                         json.dumps(["Community", "Featured"], ensure_ascii=False), json.dumps(arr(tech), ensure_ascii=False), color, 1,
                         utcnow(), utcnow()))


if MEMORY:
    MEMORY.initialize()
else:
    init_db()
seed_demo()

STATIC = ROOT / "static"
# Anything with one of these extensions is a file request, so a miss is a real 404 and never
# the app shell — a 200 text/html answer teaches browsers to cache garbage as if it were the asset.
ASSET_SUFFIXES = {".js", ".mjs", ".css", ".map", ".json", ".woff", ".woff2", ".ttf", ".otf", ".eot",
                  ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".svg", ".ico", ".html", ".txt", ".xml"}


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str):
    if full_path.startswith(("api/", "uploads/", "static/")):
        raise HTTPException(404, "Not found")
    file_path = (STATIC / full_path).resolve()
    if full_path and file_path.is_relative_to(STATIC) and file_path.is_file():
        return FileResponse(file_path)
    if full_path.endswith("/"):
        # strip(), not rstrip(): a leftover leading slash would make the Location "//host",
        # which a browser reads as a different site — an open redirect out of our own origin.
        canonical = "/" + full_path.strip("/")
        return RedirectResponse(canonical if canonical != "/" else "/")
    if Path(full_path).suffix.lower() in ASSET_SUFFIXES:
        raise HTTPException(404, "Not found")
    return FileResponse(STATIC / "index.html")
