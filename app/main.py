"""Univorya — a lightweight, real project showcase MVP.
Run: uvicorn app.main:app --reload
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("UNIVORYA_DATA_DIR", os.environ.get("UNIVORYA_DATA_DIR", str(ROOT / "data"))))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "univorya.sqlite3"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Univorya API", version="0.2.0", docs_url="/api/docs", redoc_url=None)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

CATEGORIES = ["Web App", "Mobile App", "AI & ML", "Dev Tool", "Open Source", "Design", "Idea", "Other"]
STAGES = ["Idea", "Building", "Beta", "Launched"]
MAX_FILE_BYTES = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 22_000_000


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
        CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
        CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at);
        CREATE INDEX IF NOT EXISTS idx_likes_project ON likes(project_id);
        """)


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


def slugify(s: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (slug[:60].strip("-") or "project") + "-" + uuid.uuid4().hex[:6]


def user_json(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "username": r["username"],
            "bio": r["bio"], "avatar_color": r["avatar_color"]}


def current_user(request: Request):
    raw = request.cookies.get("univorya_session")
    if not raw:
        return None
    with db() as con:
        return con.execute("""SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
                              WHERE s.token_hash=? AND s.expires > ?""",
                           (hashlib.sha256(raw.encode()).hexdigest(), int(time.time()))).fetchone()


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Please sign in to continue")
    return user


def set_session(response: Response, user_id: int, request: Request):
    token = secrets.token_urlsafe(40)
    lifetime = 60 * 60 * 24 * 30
    with db() as con:
        con.execute("DELETE FROM sessions WHERE expires <= ?", (int(time.time()),))
        con.execute("INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)",
                    (hashlib.sha256(token.encode()).hexdigest(), user_id, int(time.time()) + lifetime))
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


def arr(raw: str, max_count=8) -> list[str]:
    values = [x.strip() for x in (raw or "").split(",") if x.strip()]
    return list(dict.fromkeys(x[:35] for x in values))[:max_count]


def validate_project(title: str, tagline: str, description: str, category: str, stage: str):
    if not 3 <= len(title.strip()) <= 80:
        raise HTTPException(422, "Project name must be 3–80 characters")
    if not 10 <= len(tagline.strip()) <= 140:
        raise HTTPException(422, "Tagline must be 10–140 characters")
    if not 30 <= len(description.strip()) <= 5000:
        raise HTTPException(422, "Description must be 30–5000 characters")
    if category not in CATEGORIES or stage not in STAGES:
        raise HTTPException(422, "Choose a valid category and stage")


def project_json(row: sqlite3.Row, viewer_id: int | None = None) -> dict:
    result = dict(row)
    result["tags"] = json.loads(result["tags"])
    result["tech"] = json.loads(result["tech"])
    result["likes"] = result.pop("like_count", 0)
    result["liked"] = bool(result.pop("viewer_liked", 0))
    result["creator"] = {"name": result.pop("creator_name"), "username": result.pop("creator_username"),
                         "avatar_color": result.pop("creator_color")}
    result["is_demo"] = bool(result["is_demo"])
    result["is_owner"] = bool(viewer_id and viewer_id == result["user_id"])
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
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; connect-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'"
    )
    if request.url.path.startswith('/api/auth/'):
        resp.headers['Cache-Control'] = 'no-store' 
    return resp


@app.post("/api/auth/register", status_code=201)
def register(payload: Register, response: Response, request: Request):
    email = payload.email.strip().lower()
    username = payload.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,25}", username):
        raise HTTPException(422, "Username: 3–25 lowercase letters, numbers or underscores")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Please enter a valid email")
    with db() as con:
        try:
            cur = con.execute("INSERT INTO users(name,username,email,password_hash,created_at) VALUES(?,?,?,?,?)",
                              (payload.name.strip(), username, email, hash_password(payload.password), utcnow()))
            user_id = cur.lastrowid
            user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Email or username is already registered")
    set_session(response, user_id, request)
    return {"user": user_json(user)}


@app.post("/api/auth/login")
def login(payload: Login, response: Response, request: Request):
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE email=? OR username=?",
                           (payload.email.strip().lower(), payload.email.strip().lower())).fetchone()
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email/username or password")
    set_session(response, user["id"], request)
    return {"user": user_json(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    raw = request.cookies.get("univorya_session")
    if raw:
        with db() as con:
            con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))
    response.delete_cookie("univorya_session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"user": user_json(user) if user else None}


@app.get("/api/metadata")
def metadata():
    with db() as con:
        makers = con.execute("SELECT COUNT(*) FROM users WHERE id NOT IN (SELECT user_id FROM projects WHERE is_demo=1)").fetchone()[0]
        projects = con.execute("SELECT COUNT(*) FROM projects WHERE is_demo=0").fetchone()[0]
    return {"categories": CATEGORIES, "stages": STAGES, "community": {"makers": makers, "projects": projects}}


@app.get("/api/projects")
def list_projects(request: Request, q: str = "", category: str = "All", stage: str = "All", sort: str = "trending",
                  limit: int = 24, page: int = 1, creator: str = ""):
    if not 1 <= limit <= 60 or page < 1:
        raise HTTPException(422, "Invalid pagination")
    viewer = current_user(request)
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
    return {"projects": [project_json(row, viewer["id"] if viewer else None) for row in rows],
            "pagination": {"page": page, "limit": limit, "total": total, "pages": (total + limit - 1) // limit}}


@app.get("/api/projects/{slug}")
def project_detail(slug: str, request: Request):
    viewer = current_user(request)
    with db() as con:
        row = con.execute(PROJECT_SQL + " WHERE p.slug=?", (viewer["id"] if viewer else -1, slug)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        con.execute("UPDATE projects SET views=views+1 WHERE id=?", (row["id"],))
    result = project_json(row, viewer["id"] if viewer else None)
    result["views"] += 1
    return {"project": result}


def store_cover(cover: UploadFile | None) -> str:
    if not cover or not cover.filename:
        return ""
    raw = cover.file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, "Image must be under 5 MB")
    try:
        im = Image.open(io.BytesIO(raw))
        if im.format not in ("PNG", "JPEG", "WEBP", "GIF"):
            raise HTTPException(422, "Use PNG, JPG, WEBP or GIF images")
        if im.width * im.height > 22_000_000:
            raise HTTPException(422, "Image dimensions are too large")
        im.load()
        im = ImageOps.exif_transpose(im)
        im.thumbnail((1600, 1000), Image.Resampling.LANCZOS)
        im = im.convert("RGB")
        filename = f"{uuid.uuid4().hex}.webp"
        im.save(UPLOAD_DIR / filename, format="WEBP", quality=84, method=5)
        return f"/uploads/{filename}"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, "Image file is invalid or too large")


@app.post("/api/projects", status_code=201)
def create_project(request: Request, title: str = Form(...), tagline: str = Form(...),
                   description: str = Form(...), category: str = Form(...), stage: str = Form(...),
                   tags: str = Form(""), tech: str = Form(""), repo_url: str = Form(""),
                   demo_url: str = Form(""), website_url: str = Form(""), license: str = Form(""),
                   cover_style: str = Form("violet"), cover: UploadFile | None = File(None)):
    user = require_user(request)
    validate_project(title, tagline, description, category, stage)
    repo_url, demo_url, website_url = map(safe_url, [repo_url, demo_url, website_url])
    if len(license) > 70:
        raise HTTPException(422, "License name is too long")
    if cover_style not in ("violet", "blue", "orange", "mint", "pink", "slate"):
        cover_style = "violet"
    cover_image = store_cover(cover)
    now = utcnow()
    slug = slugify(title)
    try:
        with db() as con:
            con.execute("""INSERT INTO projects(slug,user_id,title,tagline,description,category,stage,tags,tech,
              repo_url,demo_url,website_url,license,cover_image,cover_style,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (slug, user["id"], title.strip(), tagline.strip(), description.strip(), category, stage,
               json.dumps(arr(tags)), json.dumps(arr(tech)), repo_url, demo_url, website_url, license.strip(),
               cover_image, cover_style, now, now))
    except Exception:
        if cover_image:
            (UPLOAD_DIR / Path(cover_image).name).unlink(missing_ok=True)
        raise
    return {"slug": slug, "url": f"/p/{slug}"}


@app.put("/api/projects/{slug}")
def update_project(slug: str, request: Request,
                   title: str = Form(...), tagline: str = Form(...),
                   description: str = Form(...), category: str = Form(...), stage: str = Form(...),
                   tags: str = Form(""), tech: str = Form(""), repo_url: str = Form(""),
                   demo_url: str = Form(""), website_url: str = Form(""), license: str = Form(""),
                   cover_style: str = Form("violet"), cover: UploadFile | None = File(None)):
    """Edit only the caller's project; preserve slug and older cover unless replaced."""
    user = require_user(request)
    validate_project(title, tagline, description, category, stage)
    repo_url, demo_url, website_url = map(safe_url, [repo_url, demo_url, website_url])
    if len(license) > 70:
        raise HTTPException(422, "License name is too long")
    if cover_style not in ("violet", "blue", "orange", "mint", "pink", "slate"):
        raise HTTPException(422, "Invalid cover style")
    with db() as con:
        row = con.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        if row["user_id"] != user["id"] or row["is_demo"]:
            raise HTTPException(403, "Only the creator can edit this project")
    new_cover = store_cover(cover)
    try:
        with db() as con:
            # Re-check ownership inside the write transaction to avoid a stale-read race.
            previous = con.execute("SELECT cover_image FROM projects WHERE slug=? AND user_id=? AND is_demo=0", (slug, user["id"])).fetchone()
            if not previous:
                raise HTTPException(404, "Project not found")
            con.execute("""UPDATE projects SET title=?,tagline=?,description=?,category=?,stage=?,tags=?,tech=?,
                repo_url=?,demo_url=?,website_url=?,license=?,cover_image=?,cover_style=?,updated_at=?
                WHERE slug=? AND user_id=?""",
                (title.strip(), tagline.strip(), description.strip(), category, stage,
                 json.dumps(arr(tags)), json.dumps(arr(tech)), repo_url, demo_url, website_url, license.strip(),
                 new_cover or previous["cover_image"], cover_style, utcnow(), slug, user["id"]))
        if new_cover and previous["cover_image"]:
            (UPLOAD_DIR / Path(previous["cover_image"]).name).unlink(missing_ok=True)
    except Exception:
        if new_cover:
            (UPLOAD_DIR / Path(new_cover).name).unlink(missing_ok=True)
        raise
    return {"slug": slug, "url": f"/p/{slug}"}


@app.delete("/api/projects/{slug}")
def delete_project(slug: str, request: Request):
    user = require_user(request)
    with db() as con:
        row = con.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        if row["user_id"] != user["id"]:
            raise HTTPException(403, "Only the creator can delete this project")
        con.execute("DELETE FROM projects WHERE id=?", (row["id"],))
    if row["cover_image"]:
        (UPLOAD_DIR / Path(row["cover_image"]).name).unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/projects/{slug}/like")
def like_project(slug: str, request: Request):
    user = require_user(request)
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
    return {"status": "ok", "service": "univorya"}


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
    if os.environ.get("UNIVORYA_SEED_DEMO", os.environ.get("UNIVORYA_SEED_DEMO", "1")) == "0":
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
                         json.dumps(["Community", "Featured"]), json.dumps(arr(tech)), color, 1,
                         utcnow(), utcnow()))


init_db()
seed_demo()

STATIC = ROOT / "static"

@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("uploads/"):
        raise HTTPException(404, "Not found")
    file_path = (STATIC / full_path).resolve()
    if full_path and file_path.is_relative_to(STATIC) and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(STATIC / "index.html")
