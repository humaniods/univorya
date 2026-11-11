"""Univorya Studio — per-project AI coding workspace.

The showcase stores project *listings*. Studio stores the actual source tree for an
opt-in workspace, the owner's encrypted model credential, and a small chat history.
Everything lives in a separate SQLite database so the existing SQLite/MemoryDB
showcase backends stay untouched.

Security model (read this before enabling it publicly):

* Every route is owner-only. The preview token is HMAC-signed and scoped to one
  workspace, so a leaked token cannot read another project.
* API keys are encrypted at rest with Fernet. The master key comes from
  ``UNIVORYA_SECRET_KEY`` in production; a local key file is generated otherwise.
* Commands run as the Univorya service account in the workspace directory. That is
  convenient for a self-hosted single-user install, but it is *not* a strong
  multi-tenant boundary. A per-workspace Docker runner is a production follow-up
  and is not implemented here, so keep Studio off before exposing it to untrusted
  users.
* The preview is served from a different origin whenever possible. In local mode
  it is ``http://127.0.0.1:<port>``; in production set
  ``UNIVORYA_PREVIEW_ORIGIN`` to a dedicated host such as
  ``https://preview.example.com``. That keeps an AI-generated app away from the
  Univorya session cookie and localStorage.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:  # Keep the import explicit so a missing production dependency is obvious.
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - exercised only on a broken install
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent.parent
_CONFIG: dict[str, Any] = {"root": None}
MAX_MESSAGE_CHARS = 12_000
MAX_FILE_READ = 140_000
MAX_FILE_WRITE = 300_000
MAX_TOOL_RESULT = 24_000
MAX_TOOL_ROUNDS = 8
MAX_HISTORY = 16
CHAT_LIMIT = 40          # messages per user per hour
CHAT_WINDOW = 3600
GIT_TIMEOUT = 60
GIT_PUSH_TIMEOUT = 180
GIT_MAX_OUTPUT = 18_000
CLI_TIMEOUT = int(os.environ.get("UNIVORYA_STUDIO_CLI_TIMEOUT", "300") or 300)
_WINDOWS = sys.platform.startswith("win")

PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o4-mini"],
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4.5",
        "models": ["anthropic/claude-sonnet-4.5", "openai/gpt-4.1", "google/gemini-2.5-pro",
                   "meta-llama/llama-3.3-70b-instruct"],
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama3.2",
        "models": [],
    },
    "cli": {
        "label": "Local agent CLI (Claude Code, Codex…)",
        "base_url": "",
        "model": "claude",
        "models": ["claude", "codex", "gemini", "custom"],
    },
}

# Non-interactive invocations for agent CLIs the operator has installed on the
# server. They edit files directly in the workspace, so Studio does not wrap them
# in the tool loop. A user can also provide any command that accepts {prompt}.
CLI_AGENTS = {
    "claude": {
        "label": "Claude Code",
        "binary": "claude",
        "args": ["-p", "{prompt}", "--permission-mode", "acceptEdits"],
        "env": "ANTHROPIC_API_KEY",
    },
    "codex": {
        "label": "OpenAI Codex CLI",
        "binary": "codex",
        "args": ["exec", "--full-auto", "{prompt}"],
        "env": "OPENAI_API_KEY",
    },
    "gemini": {
        "label": "Gemini CLI",
        "binary": "gemini",
        "args": ["-p", "{prompt}"],
        "env": "GEMINI_API_KEY",
    },
    "custom": {
        "label": "Custom command",
        "binary": "",
        "args": [],
        "env": "",
    },
}

TEMPLATES = {
    "static": "Static HTML / CSS / JS",
    "python": "Python FastAPI app",
    "node": "Node.js app",
    "git": "Clone an existing Git repository",
    "blank": "Empty workspace",
}

IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
                "dist", "build", ".next", ".nuxt", ".cache", ".univorya", ".mypy_cache",
                ".pytest_cache", ".ruff_cache"}
IGNORED_FILES = {".DS_Store", "Thumbs.db"}


class StudioError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --------------------------------------------------------------------------- config

def configure(data_dir: Path):
    """Point Studio at the app's data directory. Called on every app import so the
    test suite's temporary UNIVORYA_DATA_DIR is honoured even though this module is
    cached between pytest reloads."""
    root = Path(data_dir) / "studio"
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(parents=True, exist_ok=True)
    _CONFIG["root"] = root
    _init_db()


def root_dir() -> Path:
    if _CONFIG["root"] is None:
        configure(Path(os.environ.get("UNIVORYA_DATA_DIR", str(ROOT / "data"))))
    return Path(_CONFIG["root"])


def workspaces_dir() -> Path:
    path = root_dir() / "workspaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def studio_db_path() -> Path:
    return root_dir() / "studio.sqlite3"


def init_if_needed():
    root_dir()


@contextmanager
def _db():
    con = sqlite3.connect(studio_db_path(), timeout=10, check_same_thread=False)
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


def _init_db():
    with _db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS credentials (
          user_id TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          base_url TEXT NOT NULL DEFAULT '',
          api_key_enc TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workspaces (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          slug TEXT NOT NULL UNIQUE,
          user_id TEXT NOT NULL,
          template TEXT NOT NULL DEFAULT 'blank',
          status TEXT NOT NULL DEFAULT 'stopped',
          pid INTEGER NOT NULL DEFAULT 0,
          port INTEGER NOT NULL DEFAULT 0,
          command TEXT NOT NULL DEFAULT '',
          last_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          workspace_id INTEGER NOT NULL,
          role TEXT NOT NULL,
          content TEXT NOT NULL DEFAULT '',
          meta TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_workspace ON messages(workspace_id, id);
        CREATE TABLE IF NOT EXISTS git_credentials (
          user_id TEXT PRIMARY KEY,
          username TEXT NOT NULL DEFAULT 'x-access-token',
          token_enc TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(workspaces)")}
        if "last_error" not in columns:
            con.execute("ALTER TABLE workspaces ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
        if "git_remote" not in columns:
            con.execute("ALTER TABLE workspaces ADD COLUMN git_remote TEXT NOT NULL DEFAULT ''")
        if "git_branch" not in columns:
            con.execute("ALTER TABLE workspaces ADD COLUMN git_branch TEXT NOT NULL DEFAULT ''")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------ encryption

def _secret_material() -> bytes:
    raw = os.environ.get("UNIVORYA_SECRET_KEY", "").strip()
    if raw:
        return raw.encode("utf-8")
    # A self-hosted demo should work out of the box. The file is created with
    # owner-only permissions and is part of the same volume as the database.
    key_file = root_dir() / "secret.key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    return key_file.read_text(encoding="utf-8").encode("utf-8")


def _signing_key() -> bytes:
    return hashlib.sha256(_secret_material() + b"|univorya-preview").digest()


def _fernet() -> "Fernet":
    if Fernet is None:
        raise StudioError(503, "The encryption dependency is missing on the server")
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret_material()).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise StudioError(409, "The saved API key can no longer be decrypted. Reconnect your provider.")


# ------------------------------------------------------------------------ credentials

def _clean(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def _safe_base_url(raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or len(raw) > 500):
        raise StudioError(422, "Base URL must be a plain http(s) URL")
    if parsed.scheme != "https" and parsed.hostname.lower() not in ("127.0.0.1", "localhost", "::1", "[::1]"):
        # Server-side requests to internal plaintext services are an SSRF foot-gun.
        raise StudioError(422, "Use https:// for a remote model endpoint (http is only allowed on loopback)")
    return raw


def save_credential(user_id, provider: str, model: str, api_key: str, base_url: str = "") -> dict:
    provider = _clean(provider, 30).lower()
    if provider not in PROVIDERS:
        raise StudioError(422, "Choose a supported AI provider")
    model = _clean(model, 120)
    if provider == "cli":
        if model not in CLI_AGENTS:
            raise StudioError(422, "Choose Claude Code, Codex, Gemini or a custom command")
        if model == "custom":
            base_url = _clean(base_url, 500)
            if not base_url:
                raise StudioError(422, "Enter the command to run, including {prompt}")
            if "\n" in base_url or "\x00" in base_url:
                raise StudioError(422, "The command must be a single line")
        else:
            base_url = ""
    else:
        if len(model) < 1:
            raise StudioError(422, "Enter a model name")
        base_url = _safe_base_url(base_url)
    existing = get_credential(user_id)
    if api_key:
        api_key = api_key.strip()
        if not 8 <= len(api_key) <= 500:
            raise StudioError(422, "That API key does not look valid")
        if any(ch.isspace() for ch in api_key):
            raise StudioError(422, "An API key cannot contain spaces")
        encrypted = encrypt_secret(api_key)
    elif existing and existing["provider"] == provider:
        encrypted = existing["_api_key_enc"]
    elif provider == "cli":
        # The CLI usually authenticates itself (claude login / codex login). A
        # key is optional and only exported to the CLI when the user supplies one.
        encrypted = encrypt_secret("")
    else:
        raise StudioError(422, "Paste your API key")
    now = utcnow()
    with _db() as con:
        con.execute("""INSERT INTO credentials(user_id,provider,model,base_url,api_key_enc,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(user_id) DO UPDATE SET provider=excluded.provider,
                       model=excluded.model, base_url=excluded.base_url,
                       api_key_enc=excluded.api_key_enc, updated_at=excluded.updated_at""",
                    (str(user_id), provider, model, base_url, encrypted, now, now))
    return credential_public(user_id)


def get_credential(user_id) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT * FROM credentials WHERE user_id=?", (str(user_id),)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["_api_key"] = decrypt_secret(data["api_key_enc"])
    data["_api_key_enc"] = data["api_key_enc"]
    return data


def credential_public(user_id) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT * FROM credentials WHERE user_id=?", (str(user_id),)).fetchone()
    if not row:
        return None
    key = decrypt_secret(row["api_key_enc"])
    return {
        "provider": row["provider"],
        "model": row["model"],
        "base_url": row["base_url"],
        "key_hint": ("•" * 8) + key[-4:] if len(key) >= 4 else "",
        "keyless": not bool(key),
        "updated_at": row["updated_at"],
    }


def delete_credential(user_id) -> bool:
    with _db() as con:
        cur = con.execute("DELETE FROM credentials WHERE user_id=?", (str(user_id),))
    return cur.rowcount > 0


def provider_catalog() -> list[dict]:
    catalog = []
    for key, value in PROVIDERS.items():
        item = {"id": key, "label": value["label"], "base_url": value["base_url"],
                "model": value["model"], "models": value["models"]}
        if key == "cli":
            item["agents"] = {name: cfg["label"] for name, cfg in CLI_AGENTS.items()}
            item["available"] = {
                name: True if name == "custom" else bool(shutil.which(cfg["binary"]))
                for name, cfg in CLI_AGENTS.items()
            }
        catalog.append(item)
    return catalog


# ------------------------------------------------------------------------ workspaces

def _host_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")[:80] or "workspace"


def workspace_dir(slug: str) -> Path:
    return workspaces_dir() / _host_slug(slug)


def _ensure_inside(root: Path, relative: str) -> Path:
    raw = (relative or "").replace("\\", "/").strip().lstrip("/")
    if "\x00" in raw or any(part == ".." for part in raw.split("/")):
        raise StudioError(422, "That path leaves the workspace")
    target = (root / raw).resolve()
    if target != root and not target.is_relative_to(root):
        raise StudioError(422, "That path leaves the workspace")
    return target


def _scaffold(root: Path, template: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".univorya").mkdir(exist_ok=True)
    if template == "static":
        (root / "index.html").write_text(_STATIC_INDEX, encoding="utf-8")
        (root / "style.css").write_text(_STATIC_CSS, encoding="utf-8")
        (root / "app.js").write_text(_STATIC_JS, encoding="utf-8")
        (root / "univorya.json").write_text(json.dumps({
            "run": "python -m http.server {port} --bind 127.0.0.1"
        }, indent=2), encoding="utf-8")
    elif template == "python":
        (root / "main.py").write_text(_PYTHON_APP, encoding="utf-8")
        (root / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
        (root / "univorya.json").write_text(json.dumps({
            "run": "python -m uvicorn main:app --host 127.0.0.1 --port {port}"
        }, indent=2), encoding="utf-8")
    elif template == "node":
        (root / "server.js").write_text(_NODE_APP, encoding="utf-8")
        (root / "package.json").write_text(json.dumps({
            "name": "univorya-app", "private": True, "version": "0.1.0",
            "scripts": {"start": "node server.js"}
        }, indent=2), encoding="utf-8")
        (root / "univorya.json").write_text(json.dumps({
            "run": "node server.js"
        }, indent=2), encoding="utf-8")
    elif template == "git":
        # Deliberately empty: the owner clones the repository from the Git tab.
        pass
    else:
        (root / "README.md").write_text(
            "# Your Univorya Studio workspace\n\n"
            "Ask the AI to build something, or add files yourself through the chat.\n",
            encoding="utf-8")


def get_workspace(slug: str) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT * FROM workspaces WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def require_workspace(slug: str, user_id) -> dict:
    row = get_workspace(slug)
    if not row:
        raise StudioError(404, "This project has no Studio workspace yet")
    if str(row["user_id"]) != str(user_id):
        raise StudioError(403, "Only the creator can open this Studio")
    return row


def ensure_workspace(slug: str, user_id, template: str = "static") -> dict:
    template = (template or "static").lower()
    if template not in TEMPLATES:
        raise StudioError(422, "Choose a valid starter template")
    row = get_workspace(slug)
    if row:
        if str(row["user_id"]) != str(user_id):
            raise StudioError(403, "Only the creator can open this Studio")
        return row
    root = workspace_dir(slug)
    if not root.exists() or not any(root.iterdir()):
        _scaffold(root, template)
    now = utcnow()
    with _db() as con:
        try:
            cur = con.execute("""INSERT INTO workspaces(slug,user_id,template,status,created_at,updated_at)
                                 VALUES(?,?,?,'stopped',?,?)""",
                              (slug, str(user_id), template, now, now))
            workspace_id = cur.lastrowid
        except sqlite3.IntegrityError:
            row = get_workspace(slug)
            if row:
                return row
            raise
    return get_workspace(slug) or {"id": workspace_id}


def workspace_files(slug: str, user_id, max_entries: int = 250) -> list[dict]:
    row = require_workspace(slug, user_id)
    root = workspace_dir(row["slug"]).resolve()
    result: list[dict] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
        rel_dir = Path(current).relative_to(root)
        for name in sorted(files):
            if name in IGNORED_FILES:
                continue
            item = Path(current) / name
            rel = (rel_dir / name).as_posix()
            try:
                size = item.stat().st_size
            except OSError:
                continue
            result.append({"path": rel, "bytes": size})
            if len(result) >= max_entries:
                return result
    return result


def project_tree(slug: str, user_id, limit: int = 80) -> str:
    try:
        files = workspace_files(slug, user_id, max_entries=limit)
    except StudioError:
        return "(no workspace yet)"
    if not files:
        return "(empty workspace)"
    return "\n".join(f"- {item['path']} ({item['bytes']} bytes)" for item in files)


# --------------------------------------------------------------------------- git

def git_binary() -> str:
    override = os.environ.get("UNIVORYA_GIT_BINARY", "").strip()
    if override:
        return override
    found = shutil.which("git")
    if found:
        return found
    if _WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [Path("C:/Program Files/Git/cmd/git.exe")]
        if local:
            candidates.insert(0, Path(local) / "Programs" / "MinGit" / "cmd" / "git.exe")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise StudioError(503, "Git is not installed on this server")


def git_available() -> dict:
    try:
        binary = git_binary()
        result = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10)
        version = (result.stdout or "").strip()
    except (StudioError, OSError, subprocess.SubprocessError):
        return {"available": False, "version": "", "path": ""}
    return {"available": bool(version), "version": version[:120], "path": binary}


def _git_env(root: Path, token: str = "", username: str = "") -> dict:
    env = _safe_env(root)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
    if token:
        user = (username or "x-access-token").strip() or "x-access-token"
        auth = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
        # Passed through the environment, so the token never appears in argv or a
        # shell history. The child process can still read it, as any process running
        # under the same account could.
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {auth}"
    return env


def _run_git(root: Path, args: list[str], token: str = "", username: str = "",
             timeout: int = GIT_TIMEOUT, check: bool = True) -> dict:
    binary = git_binary()
    command = [binary] + [str(arg) for arg in args]
    try:
        result = subprocess.run(command, cwd=str(root), env=_git_env(root, token, username),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise StudioError(503, "Git is not installed on this server")
    except subprocess.TimeoutExpired:
        raise StudioError(504, "Git took too long and was stopped")
    except OSError as exc:
        raise StudioError(500, f"Git could not run: {exc}")
    output = (result.stdout or "")[:GIT_MAX_OUTPUT]
    error = (result.stderr or "")[:GIT_MAX_OUTPUT]
    if check and result.returncode != 0:
        detail = error.strip() or output.strip() or f"git exited with {result.returncode}"
        raise StudioError(422, detail[:1200])
    return {"code": result.returncode, "output": output, "error": error}


def _git_remote(root: Path) -> str:
    result = _run_git(root, ["remote", "get-url", "origin"], check=False)
    return result["output"].strip() if result["code"] == 0 else ""


def _git_branch(root: Path) -> str:
    result = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    return result["output"].strip() if result["code"] == 0 else ""


def _git_repo(slug: str, user_id) -> tuple[dict, Path]:
    row = require_workspace(slug, user_id)
    root = workspace_dir(slug)
    if not (root / ".git").exists():
        raise StudioError(409, "This workspace is not a Git repository yet")
    return row, root


def git_status(slug: str, user_id) -> dict:
    row = require_workspace(slug, user_id)
    root = workspace_dir(slug)
    available = git_available()
    empty = {"is_repo": False, "available": available["available"],
             "version": available["version"], "remote": row.get("git_remote") or "",
             "branch": row.get("git_branch") or "", "ahead": 0, "behind": 0,
             "changes": [], "clean": True, "log": [], "diff_stat": ""}
    if not (root / ".git").exists():
        return empty
    result = _run_git(root, ["status", "--porcelain=v1", "-b"], check=False)
    if result["code"] != 0:
        return {**empty, "is_repo": True, "error": (result["error"] or result["output"])[:800]}
    lines = result["output"].splitlines()
    branch, ahead, behind = "", 0, 0
    if lines and lines[0].startswith("## "):
        header = lines[0][3:]
        if header.startswith("No commits yet on "):
            branch = header[len("No commits yet on "):].strip()
        else:
            branch = header.split("...")[0].strip()
        match = re.search(r"\[ahead (\d+)(?:, behind (\d+))?\]", header)
        if match:
            ahead = int(match.group(1))
            behind = int(match.group(2) or 0)
        else:
            match = re.search(r"\[behind (\d+)\]", header)
            if match:
                behind = int(match.group(1))
        lines = lines[1:]
    changes = []
    for line in lines:
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changes.append({"status": line[:2].strip() or "M", "path": path})
    log_result = _run_git(root, ["log", "-6", "--pretty=format:%h%x1f%s%x1f%an%x1f%ad",
                                 "--date=short"], check=False)
    log = []
    for line in log_result["output"].splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            log.append({"hash": parts[0], "subject": parts[1], "author": parts[2],
                        "date": parts[3]})
    unstaged = _run_git(root, ["diff", "--stat"], check=False)["output"]
    staged = _run_git(root, ["diff", "--cached", "--stat"], check=False)["output"]
    return {"is_repo": True, "available": available["available"],
            "version": available["version"],
            "remote": _git_remote(root), "branch": branch or _git_branch(root),
            "ahead": ahead, "behind": behind, "changes": changes,
            "clean": not changes, "log": log,
            "diff_stat": (unstaged + staged)[:6000]}


def git_diff(slug: str, user_id, staged: bool = False) -> str:
    _row, root = _git_repo(slug, user_id)
    args = ["diff", "--no-color", "--patch"]
    if staged:
        args.append("--cached")
    result = _run_git(root, args + ["--"], check=False)
    return (result["output"] or result["error"])[:GIT_MAX_OUTPUT]


def _valid_git_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or len(raw) > 500 or raw.startswith("-") or "\n" in raw or "\x00" in raw:
        raise StudioError(422, "Enter a Git repository URL")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        if not candidate.exists():
            raise StudioError(422, "That local repository path does not exist")
        return str(candidate.resolve())
    if "://" not in raw:
        raise StudioError(422, "Use an https:// URL or an absolute local repository path")
    parsed = urlparse(raw)
    if parsed.username or parsed.password:
        raise StudioError(422, "Do not put credentials in the repository URL; save a Git token instead")
    if parsed.scheme == "https":
        if not parsed.hostname:
            raise StudioError(422, "That repository URL has no host")
        return raw
    if parsed.scheme in ("http", "file"):
        if parsed.scheme == "file":
            if not parsed.path:
                raise StudioError(422, "That file URL has no path")
            return raw
        if parsed.hostname and parsed.hostname.lower() in ("127.0.0.1", "localhost", "::1"):
            return raw
        raise StudioError(422, "Plain http is only allowed for a local Git server; use https")
    if parsed.scheme in ("ssh", "git"):
        raise StudioError(422, "Use the HTTPS clone URL; Studio cannot use SSH keys")
    raise StudioError(422, "Use an https:// URL or an absolute local repository path")


def git_clone(slug: str, user_id, url: str, branch: str = "", replace: bool = False) -> dict:
    row = require_workspace(slug, user_id)
    root = workspace_dir(slug)
    if (root / ".git").exists():
        raise StudioError(409, "This workspace is already a Git repository. Pull or push instead.")
    url = _valid_git_url(url)
    branch = _clean(branch, 120)
    if branch and not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise StudioError(422, "That branch name is not valid")
    entries = [item for item in root.iterdir() if item.name != ".univorya"]
    if entries and not replace:
        raise StudioError(409, "This workspace already has files. Enable replace to back them up "
                              "inside .univorya/backups and clone anyway.")
    stop_preview(slug, user_id)
    if entries:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = root / ".univorya" / "backups" / f"pre-clone-{stamp}"
        backup.mkdir(parents=True, exist_ok=True)
        for item in entries:
            shutil.move(str(item), str(backup / item.name))
    temp = root.parent / f".clone-{uuid.uuid4().hex[:8]}"
    args = ["clone", "--quiet"]
    if branch:
        args += ["--branch", branch]
    args += ["--", url, str(temp)]
    credential = get_git_credential(user_id)
    token = credential["_token"] if credential else ""
    username = credential["username"] if credential else ""
    try:
        _run_git(root, args, token=token, username=username, timeout=GIT_PUSH_TIMEOUT)
        for item in temp.iterdir():
            shutil.move(str(item), str(root / item.name))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    remote = _git_remote(root)
    current = _git_branch(root)
    with _db() as con:
        con.execute("UPDATE workspaces SET git_remote=?, git_branch=?, updated_at=? WHERE slug=?",
                    (remote, current, utcnow(), slug))
    return git_status(slug, user_id)


def git_commit(slug: str, user_id, message: str, paths: list[str] | None = None) -> dict:
    _row, root = _git_repo(slug, user_id)
    message = (message or "").strip()
    if not message or len(message) > 500:
        raise StudioError(422, "Write a commit message (1–500 characters)")
    if "\n" in message or "\x00" in message:
        raise StudioError(422, "Keep the commit message on one line")
    if paths:
        if len(paths) > 50:
            raise StudioError(422, "Commit at most 50 paths at once")
        targets = []
        for raw in paths:
            target = _ensure_inside(root, str(raw))
            rel = target.relative_to(root).as_posix()
            if rel:
                targets.append(rel)
        if not targets:
            raise StudioError(422, "Choose at least one workspace file")
        _run_git(root, ["add", "--"] + targets)
    else:
        _run_git(root, ["add", "--all"])
    result = _run_git(root, ["-c", "user.name=Univorya Studio",
                             "-c", "user.email=studio@univorya.local",
                             "commit", "-m", message], check=False)
    if result["code"] != 0:
        detail = (result["error"] or result["output"]).strip()
        if "nothing to commit" in detail.lower() or "no changes added" in detail.lower():
            raise StudioError(422, "Nothing to commit — the workspace has no changes")
        raise StudioError(422, detail[:1200] or "Git commit failed")
    return git_status(slug, user_id)


def git_push(slug: str, user_id) -> dict:
    _row, root = _git_repo(slug, user_id)
    if not _git_remote(root):
        raise StudioError(422, "This repository has no origin remote to push to")
    credential = get_git_credential(user_id)
    token = credential["_token"] if credential else ""
    username = credential["username"] if credential else ""
    result = _run_git(root, ["push", "-u", "origin", "HEAD"], token=token, username=username,
                      timeout=GIT_PUSH_TIMEOUT, check=False)
    if result["code"] != 0:
        detail = (result["error"] or result["output"]).strip()
        if not token:
            detail += "\nAdd a Git token in the Git tab if this repository is private."
        raise StudioError(422, detail[:1200] or "Git push failed")
    return git_status(slug, user_id)


def git_pull(slug: str, user_id) -> dict:
    _row, root = _git_repo(slug, user_id)
    credential = get_git_credential(user_id)
    token = credential["_token"] if credential else ""
    username = credential["username"] if credential else ""
    result = _run_git(root, ["pull", "--ff-only"], token=token, username=username,
                      timeout=GIT_PUSH_TIMEOUT, check=False)
    if result["code"] != 0:
        detail = (result["error"] or result["output"]).strip()
        raise StudioError(422, detail[:1200] or "Git pull failed")
    return git_status(slug, user_id)


# ------------------------------------------------------------------- git credentials

def save_git_credential(user_id, token: str, username: str = "x-access-token") -> dict:
    token = (token or "").strip()
    if not 8 <= len(token) <= 500:
        raise StudioError(422, "That Git token does not look valid")
    if any(ch.isspace() for ch in token):
        raise StudioError(422, "A Git token cannot contain spaces")
    username = _clean(username, 80) or "x-access-token"
    if any(ch.isspace() for ch in username):
        raise StudioError(422, "The Git username cannot contain spaces")
    now = utcnow()
    with _db() as con:
        con.execute("""INSERT INTO git_credentials(user_id,username,token_enc,created_at,updated_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                       token_enc=excluded.token_enc, updated_at=excluded.updated_at""",
                    (str(user_id), username, encrypt_secret(token), now, now))
    return git_credential_public(user_id)


def get_git_credential(user_id) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT * FROM git_credentials WHERE user_id=?",
                          (str(user_id),)).fetchone()
    if not row:
        return None
    return {"username": row["username"], "_token": decrypt_secret(row["token_enc"]),
            "updated_at": row["updated_at"]}


def git_credential_public(user_id) -> dict:
    with _db() as con:
        row = con.execute("SELECT * FROM git_credentials WHERE user_id=?",
                          (str(user_id),)).fetchone()
    if not row:
        return {"connected": False, "username": "", "hint": "", "updated_at": ""}
    token = decrypt_secret(row["token_enc"])
    return {"connected": True, "username": row["username"],
            "hint": ("•" * 8) + token[-4:] if len(token) >= 4 else "",
            "updated_at": row["updated_at"]}


def delete_git_credential(user_id) -> bool:
    with _db() as con:
        cur = con.execute("DELETE FROM git_credentials WHERE user_id=?", (str(user_id),))
    return cur.rowcount > 0


# --------------------------------------------------------------------------- runner

_CHAT_ATTEMPTS: dict[str, deque] = defaultdict(deque)


def _safe_env(root: Path, port: int = 0) -> dict:
    """A deliberately small environment. In particular, never inherit the app's
    MemoryDB token, Google secret or UNIVORYA_SECRET_KEY."""
    keep = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "COMSPEC", "PATHEXT")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    home = root / ".univorya" / "home"
    temp = root / ".univorya" / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    env.update({"PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "CI": "1",
                "NODE_ENV": "development", "HOST": "127.0.0.1",
                "HOME": str(home), "USERPROFILE": str(home),
                "TMPDIR": str(temp), "TMP": str(temp), "TEMP": str(temp),
                "PIP_CACHE_DIR": str(root / ".univorya" / "pip-cache"),
                "npm_config_cache": str(root / ".univorya" / "npm-cache")})
    if port:
        env["PORT"] = str(port)
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if _WINDOWS:
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=5)
            return str(pid) in out.stdout
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int):
    if not pid:
        return
    if _WINDOWS:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, timeout=10)
        except OSError:
            pass
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        time.sleep(0.4)
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _read_command(root: Path) -> str:
    config = root / "univorya.json"
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            command = str(data.get("run") or "").strip()
            if command:
                return command
        except (OSError, ValueError):
            pass
    if (root / "package.json").is_file():
        try:
            data = json.loads((root / "package.json").read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if "dev" in scripts:
                command = "npm run dev"
                if "vite" in str(scripts["dev"]).lower():
                    command += " -- --host 127.0.0.1 --port {port}"
                elif "next" in str(scripts["dev"]).lower():
                    command += " -- -p {port}"
                return command
            if "start" in scripts:
                return "npm start"
        except (OSError, ValueError):
            pass
    for python_file in ("main.py", "app.py"):
        target = root / python_file
        if target.is_file():
            try:
                text = target.read_text(encoding="utf-8", errors="replace")[:20_000]
            except OSError:
                text = ""
            module = python_file[:-3]
            if "FastAPI(" in text:
                return f"python -m uvicorn {module}:app --host 127.0.0.1 --port {{port}}"
            if "Flask(" in text:
                return f"python -m flask --app {module} run --host 127.0.0.1 --port {{port}}"
            return f"python {python_file}"
    if (root / "index.html").is_file():
        return "python -m http.server {port} --bind 127.0.0.1"
    return ""


def _format_command(command: str, port: int) -> str:
    return (command.replace("{port}", str(port))
            .replace("${PORT}", str(port)).replace("$PORT", str(port)))


def _tail(path: Path, lines: int = 60) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])[-6000:]


def run_command(slug: str, user_id, command: str, timeout_seconds: int = 120) -> dict:
    row = require_workspace(slug, user_id)
    command = (command or "").strip()
    if not command:
        raise StudioError(422, "Enter a command")
    if len(command) > 4000:
        raise StudioError(422, "That command is too long")
    timeout_seconds = max(3, min(int(timeout_seconds or 120), 300))
    root = workspace_dir(row["slug"])
    try:
        proc = subprocess.run(command, shell=True, cwd=root, env=_safe_env(root),
                              capture_output=True, text=True, errors="replace",
                              timeout=timeout_seconds,
                              creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if _WINDOWS else 0)
        output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        output += f"\n[timed out after {timeout_seconds}s]"
        code = 124
    except OSError as exc:
        raise StudioError(500, f"Could not run that command: {exc}")
    output = output.strip()
    truncated = len(output) > MAX_TOOL_RESULT
    if truncated:
        output = output[:MAX_TOOL_RESULT] + "\n… output truncated"
    return {"exit_code": code, "output": output, "truncated": truncated}


def start_preview(slug: str, user_id, command: str = "") -> dict:
    row = require_workspace(slug, user_id)
    root = workspace_dir(row["slug"])
    if not root.is_dir():
        raise StudioError(404, "Workspace folder is missing")
    stop_preview(slug, user_id)
    command = (command or "").strip() or _read_command(root)
    if not command:
        raise StudioError(422, "No run command found. Add univorya.json with a run command, "
                                "or ask the AI to set one up.")
    port = _free_port()
    final_command = _format_command(command, port)
    log = root / ".univorya" / "preview.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text(f"$ {final_command}\n", encoding="utf-8")
    creation = subprocess.CREATE_NEW_PROCESS_GROUP if _WINDOWS else 0
    try:
        with log.open("a", encoding="utf-8") as handle:
            proc = subprocess.Popen(final_command, shell=True, cwd=root, env=_safe_env(root, port),
                                    stdout=handle, stderr=subprocess.STDOUT,
                                    start_new_session=not _WINDOWS, creationflags=creation)
    except OSError as exc:
        raise StudioError(500, f"Could not start the preview: {exc}")
    deadline = time.time() + 22
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if _port_open(port):
            with _db() as con:
                con.execute("""UPDATE workspaces SET status='running',pid=?,port=?,command=?,
                               last_error='',updated_at=? WHERE id=?""",
                            (proc.pid, port, final_command, utcnow(), row["id"]))
            return preview_status(slug, user_id)
        time.sleep(0.35)
    error = _tail(log)
    _kill_tree(proc.pid)
    with _db() as con:
        con.execute("""UPDATE workspaces SET status='error',pid=0,port=0,command=?,last_error=?,
                       updated_at=? WHERE id=?""",
                    (final_command, error[-4000:], utcnow(), row["id"]))
    raise StudioError(422, f"The preview did not start. Last log lines:\n{error[-2500:]}")


def stop_preview(slug: str, user_id) -> dict:
    row = require_workspace(slug, user_id)
    _kill_tree(int(row.get("pid") or 0))
    with _db() as con:
        con.execute("""UPDATE workspaces SET status='stopped',pid=0,port=0,last_error='',
                       updated_at=? WHERE id=?""", (utcnow(), row["id"]))
    return {"status": "stopped", "running": False, "url": "", "port": 0}


def preview_status(slug: str, user_id) -> dict:
    row = require_workspace(slug, user_id)
    running = row["status"] == "running" and _pid_alive(int(row["pid"] or 0)) and _port_open(int(row["port"] or 0))
    if row["status"] == "running" and not running:
        with _db() as con:
            con.execute("""UPDATE workspaces SET status='stopped',pid=0,port=0,updated_at=? WHERE id=?""",
                        (utcnow(), row["id"]))
    status = "running" if running else ("error" if row["status"] == "error" else "stopped")
    url = ""
    if running:
        url = preview_public_url(slug, row["user_id"], row["port"])
    return {"status": status, "running": running, "url": url, "port": int(row["port"] or 0),
            "command": row["command"], "error": row["last_error"]}


def preview_log(slug: str, user_id, lines: int = 80) -> str:
    row = require_workspace(slug, user_id)
    return _tail(workspace_dir(row["slug"]) / ".univorya" / "preview.log", max(10, min(lines, 300)))


# --------------------------------------------------------------------- preview URLs

def _preview_token(slug: str, user_id, ttl: int = 12 * 3600) -> str:
    payload = f"{slug}|{user_id}|{int(time.time()) + ttl}"
    signature = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode().rstrip("=")


def verify_preview_token(token: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        slug, user_id, expiry, signature = raw.rsplit("|", 3)
        payload = f"{slug}|{user_id}|{expiry}"
        expected = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(signature, expected) or int(expiry) < time.time():
            return None
        return slug, user_id
    except (ValueError, TypeError, InvalidToken):
        return None


def preview_public_url(slug: str, user_id, port: int) -> str:
    """Local development can iframe the loopback origin directly. A deployed site
    sets UNIVORYA_PREVIEW_ORIGIN so the browser never receives a server-only
    address; the signed token route then proxies to the workspace port."""
    origin = os.environ.get("UNIVORYA_PREVIEW_ORIGIN", "").strip().rstrip("/")
    if origin:
        return f"{origin}/preview/{_preview_token(slug, user_id)}/"
    return f"http://127.0.0.1:{port}/"


def preview_target(slug: str, user_id) -> int:
    row = require_workspace(slug, user_id)
    if row["status"] != "running" or not _port_open(int(row["port"] or 0)):
        raise StudioError(409, "The preview is not running")
    return int(row["port"])


def rewrite_preview_html(text: str, prefix: str) -> str:
    """Make a root-absolute app work under /preview/<token>/.

    A full reverse proxy would need to rewrite JavaScript module graphs and Set-Cookie
    domains. This handles the common case — HTML attributes and CSS url() — and the
    live workspace is still usable from its direct origin in local mode.
    """
    if not text:
        return text
    prefix = "/" + prefix.strip("/") + "/"
    # Rewrite the app's own attributes first, then inject <base>. Doing it in the
    # other order would also prefix the base tag's own href, doubling the path.
    text = re.sub(r'(\s(?:src|href|action|poster)\s*=\s*["\'])/(?!/)', lambda m: m.group(1) + prefix, text, flags=re.I)
    text = re.sub(r"url\(\s*([\"']?)/(?!/)", lambda m: "url(" + (m.group(1) or "") + prefix, text, flags=re.I)
    if "<base " not in text[:4000].lower():
        if re.search(r"<head[^>]*>", text, re.I):
            text = re.sub(r"(<head[^>]*>)", r"\1" + f'<base href="{prefix}">', text, count=1, flags=re.I)
        else:
            text = f'<base href="{prefix}">' + text
    return text


def rewrite_preview_css(text: str, prefix: str) -> str:
    prefix = "/" + prefix.strip("/") + "/"
    return re.sub(r"url\(\s*([\"']?)/(?!/)", lambda m: "url(" + (m.group(1) or "") + prefix, text, flags=re.I)


def rewrite_location(value: str, token: str) -> str:
    if not value:
        return value
    if value.startswith("/") and not value.startswith("//"):
        return f"/preview/{token}{value}"
    parsed = urlparse(value)
    if parsed.hostname in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return f"/preview/{token}{parsed.path or '/'}" + (("?" + parsed.query) if parsed.query else "")
    return value


# ------------------------------------------------------------------------ providers

def _tool_specs() -> list[dict]:
    path = {"type": "string", "description": "Path relative to the workspace root."}
    return [
        {"name": "list_files", "description": "List the workspace files. Use this before editing.",
         "parameters": {"type": "object", "properties": {"path": path}, "additionalProperties": False}},
        {"name": "read_file", "description": "Read one text file.",
         "parameters": {"type": "object", "properties": {"path": path}, "required": ["path"]}},
        {"name": "write_file", "description": "Create or replace a text file. Always send the full content.",
         "parameters": {"type": "object", "properties": {"path": path, "content": {"type": "string"}},
                        "required": ["path", "content"]}},
        {"name": "delete_file", "description": "Delete a file inside the workspace.",
         "parameters": {"type": "object", "properties": {"path": path}, "required": ["path"]}},
        {"name": "run_command",
         "description": "Run a shell command in the workspace (install dependencies, build, test). "
                        "Do not start long-running servers here; use start_preview.",
         "parameters": {"type": "object", "properties": {
             "command": {"type": "string"},
             "timeout_seconds": {"type": "integer", "minimum": 3, "maximum": 300}},
             "required": ["command"]}},
        {"name": "start_preview",
         "description": "Start the app preview. It uses univorya.json 'run' or auto-detects the stack. "
                        "The server must listen on the PORT environment variable.",
         "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}},
        {"name": "stop_preview", "description": "Stop the running preview.",
         "parameters": {"type": "object", "properties": {}}},
        {"name": "preview_status", "description": "Check the preview status and recent log lines.",
         "parameters": {"type": "object", "properties": {}}},
        {"name": "git_status", "description": "Show the Git branch, remote, changed files and recent commits.",
         "parameters": {"type": "object", "properties": {}}},
        {"name": "git_diff", "description": "Show the current Git diff, or the staged diff.",
         "parameters": {"type": "object", "properties": {
             "staged": {"type": "boolean", "description": "Show staged changes instead of unstaged."}}}},
        {"name": "git_clone", "description": "Clone a repository into this workspace. Local paths and https "
                                             "URLs are supported. Set replace only when the user agrees.",
         "parameters": {"type": "object", "properties": {
             "url": {"type": "string"},
             "branch": {"type": "string"},
             "replace": {"type": "boolean"}}, "required": ["url"]}},
        {"name": "git_commit", "description": "Stage and commit the workspace changes with a short message.",
         "parameters": {"type": "object", "properties": {
             "message": {"type": "string"},
             "paths": {"type": "array", "items": {"type": "string"}}}, "required": ["message"]}},
        {"name": "git_push", "description": "Push the current branch to origin. Use only when the user asks.",
         "parameters": {"type": "object", "properties": {}}},
        {"name": "git_pull", "description": "Fast-forward pull from origin.",
         "parameters": {"type": "object", "properties": {}}},
    ]


def _system_prompt(slug: str, user_id) -> str:
    tree = project_tree(slug, user_id)
    return (
        "You are Univorya Studio, a pragmatic full-stack coding agent. You are editing a "
        "real project workspace through tools. Work autonomously: inspect first, then make "
        "the smallest set of complete changes, then verify. Never claim a change you did "
        "not make. Use relative paths only. The workspace is already isolated.\n\n"
        "Rules:\n"
        "- Read existing files before rewriting them.\n"
        "- Use write_file with the complete file content, never partial patches.\n"
        "- Use run_command for dependency installs, builds and tests (short commands).\n"
        "- Start the app with start_preview. The app must listen on the PORT environment "
        "variable and on 127.0.0.1. For Python use uvicorn; for Node use process.env.PORT.\n"
        "- If the preview fails, call preview_status to read the log and fix the cause.\n"
        "- Keep dependencies minimal and prefer standard-library solutions when sensible.\n"
        "- This workspace may be a Git repository. Use git_status and git_diff before committing. "
        "Commit coherent changes with a short message. Only call git_push when the user explicitly asks.\n"
        "- The user sees your final reply in a small chat; be concise and say what changed.\n"
        "- Never print API keys, environment variables, or try to read files outside the "
        "workspace. Never run destructive commands such as deleting the workspace root.\n\n"
        f"Current workspace files:\n{tree}\n"
    )


def _openai_messages(messages: list[dict], system: str) -> list[dict]:
    return [{"role": "system", "content": system}] + messages


def _openai_tools() -> list[dict]:
    return [{"type": "function", "function": {"name": item["name"],
                                              "description": item["description"],
                                              "parameters": item["parameters"]}}
            for item in _tool_specs()]


def _anthropic_tools() -> list[dict]:
    return [{"name": item["name"], "description": item["description"],
             "input_schema": item["parameters"]} for item in _tool_specs()]


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    try:
        with httpx.Client(timeout=httpx.Timeout(150, connect=20), follow_redirects=False) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise StudioError(502, f"Could not reach the AI provider: {exc}")
    if response.status_code >= 400:
        detail = "The AI provider rejected the request"
        try:
            data = response.json()
            message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
            if isinstance(message, str) and message:
                detail = message[:500]
        except ValueError:
            pass
        if response.status_code in (401, 403):
            detail = "Your API key was rejected. Reconnect it in Studio settings."
        raise StudioError(502 if response.status_code >= 500 else 422, detail)
    try:
        return response.json()
    except ValueError:
        raise StudioError(502, "The AI provider returned an invalid response")


def _call_openai(cred: dict, messages: list[dict], system: str) -> dict:
    base = (cred.get("base_url") or PROVIDERS[cred["provider"]]["base_url"]).rstrip("/")
    headers = {"Authorization": f"Bearer {cred['_api_key']}", "Content-Type": "application/json"}
    if cred["provider"] == "openrouter":
        headers.update({"HTTP-Referer": "https://univorya.app", "X-Title": "Univorya Studio"})
    data = _post_json(base + "/chat/completions", headers, {
        "model": cred["model"], "messages": _openai_messages(messages, system),
        "tools": _openai_tools(), "tool_choice": "auto", "temperature": 0.2,
    })
    choices = data.get("choices") or []
    if not choices:
        raise StudioError(502, "The AI provider returned no answer")
    message = choices[0].get("message") or {}
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            args = json.loads(function.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append({"id": call.get("id") or uuid.uuid4().hex,
                      "name": function.get("name") or "", "args": args if isinstance(args, dict) else {}})
    return {"text": (message.get("content") or "").strip(), "tool_calls": calls,
            "usage": data.get("usage") or {}}


def _call_anthropic(cred: dict, messages: list[dict], system: str) -> dict:
    base = (cred.get("base_url") or PROVIDERS["anthropic"]["base_url"]).rstrip("/")
    converted = []
    for item in messages:
        if item["role"] == "assistant":
            blocks = []
            if item.get("content"):
                blocks.append({"type": "text", "text": item["content"]})
            for call in item.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                               "input": call.get("args") or {}})
            converted.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif item["role"] == "tool":
            converted.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": item["tool_call_id"],
                "content": item.get("content") or "",
            }]})
        else:
            converted.append({"role": "user", "content": item.get("content") or ""})
    data = _post_json(base + "/v1/messages", {
        "x-api-key": cred["_api_key"], "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }, {"model": cred["model"], "max_tokens": 4096, "system": system,
        "messages": converted, "tools": _anthropic_tools()})
    text_parts, calls = [], []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            calls.append({"id": block.get("id") or uuid.uuid4().hex,
                          "name": block.get("name") or "",
                          "args": block.get("input") if isinstance(block.get("input"), dict) else {}})
    return {"text": "".join(text_parts).strip(), "tool_calls": calls,
            "usage": data.get("usage") or {}}


def _call_model(cred: dict, messages: list[dict], system: str) -> dict:
    if cred["provider"] == "anthropic":
        return _call_anthropic(cred, messages, system)
    return _call_openai(cred, messages, system)


# ------------------------------------------------------------------------- cli agent

def _split_command(template: str) -> list[str]:
    try:
        parts = shlex.split(template, posix=not _WINDOWS)
    except ValueError:
        raise StudioError(422, "That command could not be parsed")
    if _WINDOWS:
        parts = [part.strip('"') for part in parts]
    return [part for part in parts if part]


def _cli_argv(cred: dict, prompt: str) -> list[str]:
    model = cred.get("model") or ""
    if model == "custom":
        template = cred.get("base_url") or ""
        if not template:
            raise StudioError(422, "Save a custom command first")
        parts = _split_command(template)
        if not parts:
            raise StudioError(422, "That command is empty")
        if not any("{prompt}" in part for part in parts):
            raise StudioError(422, "The custom command must include {prompt}")
        return [part.replace("{prompt}", prompt) for part in parts]
    config = CLI_AGENTS.get(model)
    if not config:
        raise StudioError(422, "Choose a supported agent CLI")
    return [config["binary"]] + [part.replace("{prompt}", prompt) for part in config["args"]]


def _call_cli(cred: dict, messages: list[dict], system: str, slug: str, user_id) -> dict:
    root = workspace_dir(slug)
    if not root.exists():
        raise StudioError(404, "This workspace has no files yet")
    last_user = ""
    for item in reversed(messages):
        if item.get("role") == "user":
            last_user = str(item.get("content") or "")
            break
    if not last_user:
        raise StudioError(422, "Write a message first")
    prompt = (system + "\n\nUser request:\n" + last_user)[:MAX_MESSAGE_CHARS + 6000]
    argv = _cli_argv(cred, prompt)
    binary = argv[0]
    resolved = shutil.which(binary)
    if not resolved and Path(binary).is_file():
        resolved = str(Path(binary).resolve())
    if not resolved:
        label = CLI_AGENTS.get(cred.get("model") or "", {}).get("label", binary)
        raise StudioError(422, f"{label} is not installed on this server. Install `{binary}` "
                              "or use an API key provider instead.")
    argv[0] = resolved
    env = _safe_env(root)
    # Agent CLIs keep their login in the account's home directory. Reuse the real
    # home so an already-authenticated `claude`/`codex` works; the optional key is
    # still exported only to that child process.
    real_home = os.environ.get("USERPROFILE" if _WINDOWS else "HOME", "").strip()
    if real_home and Path(real_home).is_dir():
        env["HOME"] = real_home
        if _WINDOWS:
            env["USERPROFILE"] = real_home
    key = cred.get("_api_key") or ""
    env_name = CLI_AGENTS.get(cred.get("model") or "", {}).get("env", "")
    if key and env_name:
        env[env_name] = key
    try:
        result = subprocess.run(argv, cwd=str(root), env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT,
                                stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise StudioError(422, f"{binary} is not installed on this server")
    except subprocess.TimeoutExpired:
        raise StudioError(504, f"The agent CLI did not finish within {CLI_TIMEOUT} seconds")
    except OSError as exc:
        raise StudioError(500, f"The agent CLI could not start: {exc}")
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        detail = (output + "\n" + error).strip() or f"exited with {result.returncode}"
        raise StudioError(502, f"The agent CLI failed: {detail[-1500:]}")
    text = output or error or "The agent CLI finished without a reply."
    return {"text": text[:MAX_TOOL_RESULT], "tool_calls": [], "usage": {"cli": cred["model"]}}


# --------------------------------------------------------------------------- tools

def _tool_list_files(slug: str, user_id, args: dict) -> str:
    path = str(args.get("path") or "")
    root = workspace_dir(slug)
    target = _ensure_inside(root, path)
    if not target.exists():
        return f"Path not found: {path or '.'}"
    if target.is_file():
        return target.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_READ]
    files = []
    for item in sorted(target.rglob("*")):
        if item.is_dir() or any(part in IGNORED_DIRS for part in item.relative_to(root).parts):
            continue
        rel = item.relative_to(root).as_posix()
        try:
            files.append(f"{rel} ({item.stat().st_size} bytes)")
        except OSError:
            continue
        if len(files) >= 400:
            files.append("… more files omitted")
            break
    return "\n".join(files) or "(empty directory)"


def _tool_read_file(slug: str, user_id, args: dict) -> str:
    target = _ensure_inside(workspace_dir(slug), str(args.get("path") or ""))
    if not target.is_file():
        return f"File not found: {args.get('path')}"
    raw = target.read_bytes()
    if b"\x00" in raw[:4000]:
        return "That file looks binary and cannot be read as text."
    text = raw[:MAX_FILE_READ].decode("utf-8", errors="replace")
    if len(raw) > MAX_FILE_READ:
        text += "\n… file truncated"
    return text


def _tool_write_file(slug: str, user_id, args: dict) -> str:
    target = _ensure_inside(workspace_dir(slug), str(args.get("path") or ""))
    content = str(args.get("content") or "")
    if len(content.encode("utf-8")) > MAX_FILE_WRITE:
        raise StudioError(422, "That file is too large for one write")
    if target == workspace_dir(slug).resolve() or target.is_dir():
        return "Choose a file path, not the workspace root."
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f".tmp-{uuid.uuid4().hex[:6]}")
    temp.write_text(content, encoding="utf-8", newline="\n")
    temp.replace(target)
    return f"Wrote {len(content)} characters to {args.get('path')}"


def _tool_delete_file(slug: str, user_id, args: dict) -> str:
    target = _ensure_inside(workspace_dir(slug), str(args.get("path") or ""))
    if not target.exists():
        return "That path does not exist."
    if target.is_dir():
        try:
            target.rmdir()
            return f"Removed directory {args.get('path')}"
        except OSError:
            return "Directory is not empty; delete its files first."
    target.unlink()
    return f"Deleted {args.get('path')}"


def execute_tool(slug: str, user_id, name: str, args: dict) -> str:
    if name == "list_files":
        return _tool_list_files(slug, user_id, args)
    if name == "read_file":
        return _tool_read_file(slug, user_id, args)
    if name == "write_file":
        return _tool_write_file(slug, user_id, args)
    if name == "delete_file":
        return _tool_delete_file(slug, user_id, args)
    if name == "run_command":
        result = run_command(slug, user_id, str(args.get("command") or ""),
                             int(args.get("timeout_seconds") or 120))
        return f"exit_code: {result['exit_code']}\n{result['output']}"
    if name == "start_preview":
        status = start_preview(slug, user_id, str(args.get("command") or ""))
        return f"Preview running at {status['url']} (port {status['port']})."
    if name == "stop_preview":
        stop_preview(slug, user_id)
        return "Preview stopped."
    if name == "preview_status":
        status = preview_status(slug, user_id)
        log = preview_log(slug, user_id) if status["running"] or status["status"] == "error" else ""
        return json.dumps({**status, "log": log}, ensure_ascii=False)
    if name == "git_status":
        return json.dumps(git_status(slug, user_id), ensure_ascii=False)
    if name == "git_diff":
        return git_diff(slug, user_id, bool(args.get("staged")))
    if name == "git_clone":
        status = git_clone(slug, user_id, str(args.get("url") or ""),
                           str(args.get("branch") or ""), bool(args.get("replace")))
        return "Cloned. " + json.dumps({"remote": status["remote"], "branch": status["branch"],
                                        "clean": status["clean"]}, ensure_ascii=False)
    if name == "git_commit":
        paths = args.get("paths") if isinstance(args.get("paths"), list) else None
        status = git_commit(slug, user_id, str(args.get("message") or ""), paths)
        return "Committed. " + json.dumps({"branch": status["branch"],
                                           "clean": status["clean"]}, ensure_ascii=False)
    if name == "git_push":
        status = git_push(slug, user_id)
        return "Pushed. " + json.dumps({"branch": status["branch"],
                                        "ahead": status["ahead"]}, ensure_ascii=False)
    if name == "git_pull":
        status = git_pull(slug, user_id)
        return "Pulled. " + json.dumps({"branch": status["branch"],
                                        "behind": status["behind"]}, ensure_ascii=False)
    return f"Unknown tool: {name}"


# --------------------------------------------------------------------------- chat

def _check_chat_budget(user_id):
    key = str(user_id)
    bucket = _CHAT_ATTEMPTS[key]
    now = time.time()
    while bucket and now - bucket[0] > CHAT_WINDOW:
        bucket.popleft()
    if len(bucket) >= CHAT_LIMIT:
        raise StudioError(429, "Too many Studio messages. Please wait a little and try again.")
    bucket.append(now)


def messages_for(slug: str, user_id, limit: int = 80) -> list[dict]:
    row = require_workspace(slug, user_id)
    with _db() as con:
        rows = con.execute("SELECT * FROM messages WHERE workspace_id=? ORDER BY id DESC LIMIT ?",
                           (row["id"], limit)).fetchall()
    result = []
    for item in reversed(rows):
        try:
            meta = json.loads(item["meta"] or "{}")
        except ValueError:
            meta = {}
        result.append({"id": item["id"], "role": item["role"], "content": item["content"],
                       "meta": meta, "created_at": item["created_at"]})
    return result


def _save_message(workspace_id: int, role: str, content: str, meta: dict | None = None):
    with _db() as con:
        cur = con.execute("""INSERT INTO messages(workspace_id,role,content,meta,created_at)
                             VALUES(?,?,?,?,?)""",
                          (workspace_id, role, content, json.dumps(meta or {}, ensure_ascii=False), utcnow()))
        return cur.lastrowid


def _history_messages(slug: str, user_id) -> list[dict]:
    history = [m for m in messages_for(slug, user_id, limit=MAX_HISTORY * 2) if m["role"] in ("user", "assistant")]
    # Only text turns are replayed. Tool transcripts stay in the UI; the files on disk
    # are the durable memory, and replaying stale tool ids is provider-specific pain.
    return [{"role": m["role"], "content": m["content"]} for m in history[-MAX_HISTORY:] if m["content"]]


def chat(slug: str, user_id, text: str) -> dict:
    row = require_workspace(slug, user_id)
    text = (text or "").strip()
    if not text:
        raise StudioError(422, "Write a message first")
    if len(text) > MAX_MESSAGE_CHARS:
        raise StudioError(422, "That message is too long")
    cred = get_credential(user_id)
    if not cred:
        raise StudioError(409, "Connect an OpenAI, Claude or OpenRouter key first")
    _check_chat_budget(user_id)

    system = _system_prompt(slug, user_id)
    history = _history_messages(slug, user_id)
    messages: list[dict] = history + [{"role": "user", "content": text}]
    user_message_id = _save_message(row["id"], "user", text)
    events: list[dict] = []
    final_text = ""

    if cred["provider"] == "cli":
        reply = _call_cli(cred, messages, system, slug, user_id)
        final_text = reply["text"] or "The agent CLI finished."
        events = [{"name": "cli_agent", "args": {"agent": cred["model"]}, "ok": True,
                   "result": final_text[:MAX_TOOL_RESULT]}]
        assistant_id = _save_message(row["id"], "assistant", final_text, {"events": events})
        return {"message": {"id": assistant_id, "role": "assistant", "content": final_text,
                            "meta": {"events": events}},
                "user_message_id": user_message_id,
                "events": events,
                "preview": preview_status(slug, user_id)}

    for _ in range(MAX_TOOL_ROUNDS):
        reply = _call_model(cred, messages, system)
        assistant_text = reply.get("text") or ""
        tool_calls = reply.get("tool_calls") or []
        if assistant_text:
            final_text = assistant_text
        if not tool_calls:
            break
        # Persist the assistant's tool decision so the transcript makes sense, then run
        # each call. Tool output is kept out of the model's next-turn text history only
        # for our own DB; the in-flight `messages` list gets the real result.
        messages.append({"role": "assistant", "content": assistant_text, "tool_calls": tool_calls})
        for call in tool_calls:
            name = call.get("name") or ""
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            try:
                result = execute_tool(slug, user_id, name, args)
                ok = True
            except StudioError as exc:
                result = f"Error: {exc.detail}"
                ok = False
            except Exception as exc:  # a tool crash must not kill the whole turn
                result = f"Error: {type(exc).__name__}: {exc}"
                ok = False
            result = result[:MAX_TOOL_RESULT]
            events.append({"name": name, "args": args, "ok": ok, "result": result})
            _save_message(row["id"], "tool", result, {"name": name, "args": args, "ok": ok})
            messages.append({"role": "tool", "tool_call_id": call.get("id") or uuid.uuid4().hex,
                             "name": name, "content": result})
    else:
        final_text = final_text or "I reached the tool limit for this turn. Check the preview and ask me to continue."

    if not final_text:
        final_text = "I finished the changes. Check the preview and tell me what to adjust."
    assistant_id = _save_message(row["id"], "assistant", final_text, {"events": events})
    return {"message": {"id": assistant_id, "role": "assistant", "content": final_text,
                        "meta": {"events": events}},
            "user_message_id": user_message_id,
            "events": events,
            "preview": preview_status(slug, user_id)}


# -------------------------------------------------------------------- starter files

_STATIC_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Your app</title>
  <link rel="stylesheet" href="./style.css"/>
</head>
<body>
  <main class="card">
    <span class="badge">UNIVORYA STUDIO</span>
    <h1>Your app starts here.</h1>
    <p>Ask the AI to change this page, add a feature, or build something new.</p>
    <button id="action">Try me</button>
    <p id="status" class="status">Ready.</p>
  </main>
  <script src="./app.js"></script>
</body>
</html>
"""

_STATIC_CSS = """*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#f6f7fb;
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.card{width:min(560px,90vw);padding:48px;border-radius:28px;background:linear-gradient(145deg,#151b35,#0d1226);
border:1px solid rgba(255,255,255,.12);box-shadow:0 30px 80px rgba(0,0,0,.45)}
.badge{font-size:11px;letter-spacing:.18em;color:#a78bfa;font-weight:700}
h1{font-size:clamp(34px,6vw,56px);line-height:1;margin:18px 0 14px;letter-spacing:-.04em}
p{color:#b8bfd4;line-height:1.6}
button{margin-top:18px;padding:13px 22px;border:0;border-radius:12px;background:#7c5cff;color:white;
font-size:15px;font-weight:700;cursor:pointer}
button:hover{background:#8f74ff}
.status{font-size:13px;margin-top:14px}
"""

_STATIC_JS = """const button = document.getElementById('action');
const status = document.getElementById('status');
let count = 0;
button.addEventListener('click', () => {
  count += 1;
  status.textContent = `Clicked ${count} time${count === 1 ? '' : 's'}. Now ask the AI for a real feature.`;
});
"""

_PYTHON_APP = """from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Univorya Studio app")


@app.get("/", response_class=HTMLResponse)
def home():
    return '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Your FastAPI app</title>
  <style>
    body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#f6f7fb;
      font-family:ui-sans-serif,system-ui,sans-serif}
    main{padding:48px;border-radius:28px;background:#141a30;border:1px solid #2a3355;max-width:560px}
    h1{font-size:42px;margin:0 0 12px;letter-spacing:-.04em} p{color:#b8bfd4;line-height:1.6}
    code{background:#0b1020;padding:3px 7px;border-radius:6px;color:#a78bfa}
  </style>
</head>
<body><main><h1>FastAPI is running.</h1>
<p>Ask Studio to add routes, a database, or a frontend. This page is served by <code>main.py</code>.</p>
</main></body>
</html>'''


@app.get("/api/health")
def health():
    return {"status": "ok"}
"""

_NODE_APP = """const http = require('node:http');
const port = Number(process.env.PORT || 3000);

const html = `<!doctype html>
<html lang="en">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Your Node app</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#f6f7fb;
font-family:ui-sans-serif,system-ui,sans-serif}
main{padding:48px;border-radius:28px;background:#141a30;border:1px solid #2a3355;max-width:560px}
h1{font-size:42px;margin:0 0 12px;letter-spacing:-.04em}p{color:#b8bfd4;line-height:1.6}
code{background:#0b1020;padding:3px 7px;border-radius:6px;color:#a78bfa}
</style></head>
<body><main><h1>Node is running.</h1>
<p>Ask Studio to add routes, a frontend, or a database. This page is served by <code>server.js</code>.</p>
</main></body></html>`;

http.createServer((req, res) => {
  if (req.url === '/api/health') {
    res.writeHead(200, {'content-type': 'application/json'});
    return res.end(JSON.stringify({status: 'ok'}));
  }
  res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
  res.end(html);
}).listen(port, '127.0.0.1', () => console.log(`listening on ${port}`));
"""
