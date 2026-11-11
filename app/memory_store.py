"""MemoryDB-backed persistence for Univorya.

Univorya's production store is `MemoryDB <https://github.com/humaniods/MemoryDB>`__:
one JSON document engine for users, sessions, projects and likes, with image files
still kept on local disk.  SQLite remains the zero-dependency development fallback
in :mod:`app.main`.

The four collections below are private to the application principal that owns the
MemoryDB token.  Every write goes through the documented HTTP API:

* ``univorya_users``     - accounts (scrypt hashes, never plaintext passwords)
* ``univorya_sessions``  - hashed session tokens, with a real record-level expiry
* ``univorya_projects``  - project documents, media metadata embedded in each row
* ``univorya_likes``     - one document per (user, project), unique ``key`` field

Only the standard library is used, so the image needs no extra Python package.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

USERS = "univorya_users"
SESSIONS = "univorya_sessions"
PROJECTS = "univorya_projects"
LIKES = "univorya_likes"


class MemoryDBError(Exception):
    """A non-2xx answer or an unreachable MemoryDB server."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class DuplicateRecord(MemoryDBError):
    """A unique field already holds the value being written."""


class RecordNotFound(MemoryDBError):
    """The record does not exist, was deleted, or has expired."""


class VersionConflict(MemoryDBError):
    """An optimistic ``version`` check lost a race."""


def _rfc3339(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return _rfc3339(time.time())


def _search_text(data: dict) -> str:
    parts = [data.get("title", ""), data.get("tagline", ""), data.get("description", "")]
    parts.extend(data.get("tags") or [])
    parts.extend(data.get("tech") or [])
    return " ".join(str(part) for part in parts if part)


class MemoryDBClient:
    """Small, dependency-free HTTP client for the MemoryDB v0.2 API."""

    def __init__(self, connection_string: str, timeout: float = 35):
        raw = (connection_string or "").strip()
        parts = urlsplit(raw)
        if parts.scheme in ("memorydb", "memorydbs"):
            token = unquote(parts.username or "")
            host = parts.hostname or "127.0.0.1"
            scheme = "https" if parts.scheme == "memorydbs" else "http"
            port = parts.port or (443 if scheme == "https" else 8080)
        elif parts.scheme in ("http", "https"):
            token = os.environ.get("MEMORYDB_TOKEN", "").strip()
            host = parts.hostname or "127.0.0.1"
            port = parts.port or (443 if parts.scheme == "https" else 8080)
            scheme = parts.scheme
        else:
            raise ValueError(
                "MEMORYDB_URL must look like memorydb://TOKEN@host:port "
                "or http://host:port with MEMORYDB_TOKEN set"
            )
        if not token:
            raise ValueError("MEMORYDB_URL is missing its bearer token")
        self.base = f"{scheme}://{host}:{port}/api/v1/"
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body=None, headers=None, timeout=None):
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            self.base + path.lstrip("/"),
            data=payload,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                **(headers or {}),
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
            except (ValueError, UnicodeDecodeError):
                message = str(exc)
            if exc.code == 404:
                raise RecordNotFound(exc.code, message) from exc
            if exc.code == 409:
                raise VersionConflict(exc.code, message) from exc
            raise MemoryDBError(exc.code, message) from exc
        except (URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise MemoryDBError(0, f"MemoryDB unreachable: {reason}") from exc

    def health(self) -> dict:
        return self.request("GET", "health/", timeout=5)


SCHEMAS = {
    USERS: {
        "fields": {
            "name": {"type": "string", "required": True},
            "username": {"type": "string", "required": True, "casefold": True},
            "email": {"type": "string", "required": True, "casefold": True},
            "password_hash": {"type": "string", "required": True},
            "bio": {"type": "string"},
            "avatar_color": {"type": "string"},
            "created_at": {"type": "string", "required": True},
            "is_demo": {"type": "boolean"},
            "google_sub": {"type": "string"},
            "auth_provider": {"type": "string"},
        },
        "unique": ["username", "email"],
        "indexes": ["username", "email", "is_demo", "google_sub"],
        "strict": False,
    },
    SESSIONS: {
        "fields": {
            "token_hash": {"type": "string", "required": True},
            "user_id": {"type": "string", "required": True},
            "created_at": {"type": "string", "required": True},
        },
        "unique": ["token_hash"],
        "indexes": ["token_hash", "user_id"],
        "strict": False,
    },
    PROJECTS: {
        "fields": {
            "slug": {"type": "string", "required": True},
            "user_id": {"type": "string", "required": True},
            "title": {"type": "string", "required": True},
            "tagline": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string"},
            "stage": {"type": "string"},
            "tags": {"type": "array"},
            "tech": {"type": "array"},
            "repo_url": {"type": "string"},
            "demo_url": {"type": "string"},
            "website_url": {"type": "string"},
            "preview_url": {"type": "string"},
            "license": {"type": "string"},
            "cover_image": {"type": "string"},
            "cover_style": {"type": "string"},
            "is_demo": {"type": "boolean"},
            "created_at": {"type": "string"},
            "updated_at": {"type": "string"},
            "views": {"type": "integer"},
            "likes": {"type": "integer"},
            "creator_name": {"type": "string"},
            "creator_username": {"type": "string"},
            "creator_color": {"type": "string"},
            "search_text": {"type": "string"},
            "media": {"type": "array"},
        },
        "unique": ["slug"],
        "indexes": [
            "slug", "user_id", "category", "stage", "is_demo",
            "created_at", "views", "likes", "creator_username",
        ],
        "strict": False,
    },
    LIKES: {
        "fields": {
            "key": {"type": "string", "required": True},
            "user_id": {"type": "string", "required": True},
            "project_id": {"type": "string", "required": True},
            "created_at": {"type": "string", "required": True},
        },
        "unique": ["key"],
        "indexes": ["key", "user_id", "project_id"],
        "strict": False,
    },
}


class MemoryStore:
    """Univorya persistence implemented as MemoryDB document operations."""

    def __init__(self, connection_string: str, timeout: float = 35):
        self.client = MemoryDBClient(connection_string, timeout)
        # One process owns the app's like toggles.  MemoryDB itself is single-node;
        # running one Uvicorn worker keeps create/delete pairs from racing.
        self._like_lock = threading.Lock()

    # ------------------------------------------------------------------ setup

    def initialize(self, attempts: int = 30, delay: float = 1.0) -> "MemoryStore":
        """Wait for MemoryDB and create the collections if this is a fresh journal."""
        last = None
        for attempt in range(attempts):
            try:
                self.client.health()
                existing = {
                    record.get("collection")
                    for record in self.client.request("GET", "collections/").get("results", [])
                }
                for name, schema in SCHEMAS.items():
                    if name in existing:
                        continue
                    try:
                        self.client.request("POST", "collections/", {"name": name, "schema": schema})
                    except VersionConflict:
                        # Another app instance created it between our list and write.
                        pass
                return self
            except (MemoryDBError, OSError) as exc:
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(min(delay + attempt * 0.5, 5.0))
        raise RuntimeError(f"MemoryDB is not ready after {attempts} attempts: {last}")

    def health(self) -> dict:
        return self.client.health()

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _doc(record: dict) -> dict:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id", "")
        data["version"] = record.get("version", 0)
        return data

    def _create(self, collection, data, *, expires=None, idempotency_key=None):
        body = {"data": data, "visibility": "private"}
        if expires:
            body["expires"] = expires
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        try:
            record = self.client.request(
                "POST", f"documents/{collection}/", body, headers
            )
        except VersionConflict as exc:
            raise DuplicateRecord(exc.status, str(exc)) from exc
        return self._doc(record)

    def _get(self, collection, pk):
        try:
            return self._doc(
                self.client.request("GET", f"documents/{collection}/{quote(pk, safe='')}/")
            )
        except RecordNotFound:
            return None

    def _update(self, collection, pk, *, version=None, set_fields=None, unset=None, inc=None):
        body = {}
        if version is not None:
            body["version"] = int(version)
        if set_fields:
            body["set"] = set_fields
        if unset:
            body["unset"] = unset
        if inc:
            body["inc"] = inc
        record = self.client.request(
            "PATCH", f"documents/{collection}/{quote(pk, safe='')}/", body
        )
        return self._doc(record)

    def _delete(self, collection, pk, version):
        return self.client.request(
            "DELETE",
            f"documents/{collection}/{quote(pk, safe='')}/",
            headers={"If-Match": str(version)},
        )

    def _query(self, collection, *, where=None, or_groups=None, order_by=None,
               limit=50, offset=0, projection=None):
        body = {"limit": max(1, min(int(limit), 500))}
        if where:
            body["where"] = where
        if or_groups:
            body["or"] = or_groups
        if order_by:
            body["order_by"] = order_by
        if offset:
            body["offset"] = int(offset)
        if projection:
            body["projection"] = projection
        page = self.client.request("POST", f"query/{collection}/", body)
        return [self._doc(record) for record in page.get("results", [])], int(page.get("total", 0))

    def _all(self, collection, *, where=None, or_groups=None, order_by=None):
        rows, total = [], 0
        offset = 0
        while True:
            page, total = self._query(
                collection, where=where, or_groups=or_groups, order_by=order_by,
                limit=500, offset=offset,
            )
            rows.extend(page)
            offset += len(page)
            if not page or offset >= total or offset >= 1_000_000:
                return rows, total

    # ---------------------------------------------------------------- users

    def create_user(self, *, name, username, email, password_hash,
                    is_demo=False, bio="", avatar_color="#8b5cf6", created_at=None,
                    google_sub="", auth_provider="password"):
        data = {
            "name": name,
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "bio": bio,
            "avatar_color": avatar_color,
            "created_at": created_at or _now_iso(),
            "is_demo": bool(is_demo),
            "auth_provider": auth_provider,
        }
        if google_sub:
            data["google_sub"] = google_sub
        return self._create(USERS, data, idempotency_key=f"user-{username}")

    def find_user_by_username(self, username: str):
        rows, _ = self._query(
            USERS,
            where=[{"field": "username", "op": "iequals", "value": username.strip().lower()}],
            limit=1,
        )
        return rows[0] if rows else None

    def find_user_by_identifier(self, identifier: str):
        value = identifier.strip().lower()
        rows, _ = self._query(
            USERS,
            or_groups=[
                [{"field": "email", "op": "iequals", "value": value}],
                [{"field": "username", "op": "iequals", "value": value}],
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def find_user_by_email(self, email: str):
        rows, _ = self._query(
            USERS,
            where=[{"field": "email", "op": "iequals", "value": email.strip().lower()}],
            limit=1,
        )
        return rows[0] if rows else None

    def find_user_by_google_sub(self, sub: str):
        rows, _ = self._query(
            USERS,
            where=[{"field": "google_sub", "op": "eq", "value": sub.strip()}],
            limit=1,
        )
        return rows[0] if rows else None

    def link_google_account(self, user: dict, sub: str):
        """Attach a verified Google subject to an existing account.

        MemoryDB has no schema migration: the new fields simply appear on the
        document that needs them, and older rows keep working as password accounts.
        """
        return self._update(
            USERS, user["id"], version=user.get("version"),
            set_fields={"google_sub": sub, "auth_provider": "google"},
        )

    def get_user(self, user_id):
        return self._get(USERS, user_id)

    def community_counts(self):
        makers = self._query(
            USERS, where=[{"field": "is_demo", "op": "eq", "value": False}], limit=1
        )[1]
        projects = self._query(
            PROJECTS, where=[{"field": "is_demo", "op": "eq", "value": False}], limit=1
        )[1]
        return {"makers": makers, "projects": projects}

    # ------------------------------------------------------------- sessions

    def create_session(self, token_hash: str, user_id, lifetime: int):
        data = {
            "token_hash": token_hash,
            "user_id": str(user_id),
            "created_at": _now_iso(),
        }
        return self._create(
            SESSIONS, data,
            expires=_rfc3339(time.time() + lifetime),
            idempotency_key=f"session-{token_hash[:40]}",
        )

    def user_for_session(self, token_hash: str):
        rows, _ = self._query(
            SESSIONS,
            where=[{"field": "token_hash", "op": "eq", "value": token_hash}],
            limit=1,
        )
        if not rows:
            return None
        return self.get_user(rows[0].get("user_id"))

    def delete_session(self, token_hash: str):
        rows, _ = self._query(
            SESSIONS,
            where=[{"field": "token_hash", "op": "eq", "value": token_hash}],
            limit=1,
        )
        if not rows:
            return
        try:
            self._delete(SESSIONS, rows[0]["id"], rows[0]["version"])
        except (RecordNotFound, VersionConflict):
            pass

    # ------------------------------------------------------------- projects

    @staticmethod
    def _decorate_project(row: dict, liked: set[str]) -> dict:
        row["like_count"] = int(row.get("likes", 0) or 0)
        row["viewer_liked"] = 1 if row["id"] in liked else 0
        row["creator_name"] = row.get("creator_name", "")
        row["creator_username"] = row.get("creator_username", "")
        row["creator_color"] = row.get("creator_color", "#8b5cf6")
        row["media"] = row.get("media") or []
        return row

    def _liked_ids(self, viewer_id, project_ids) -> set[str]:
        if not viewer_id or not project_ids:
            return set()
        rows, _ = self._query(
            LIKES,
            where=[
                {"field": "user_id", "op": "eq", "value": str(viewer_id)},
                {"field": "project_id", "op": "in", "value": list(project_ids)},
            ],
            limit=min(len(project_ids), 500),
        )
        return {row["project_id"] for row in rows}

    def list_projects(self, *, q="", category="All", stage="All", creator="",
                      sort="trending", limit=24, page=1, viewer_id=None):
        where, or_groups = [], None
        term = (q or "").strip()[:100]
        if term:
            or_groups = [
                [{"field": field, "op": "icontains", "value": term}]
                for field in ("title", "tagline", "description", "search_text")
            ]
        if category != "All":
            where.append({"field": "category", "op": "eq", "value": category})
        if stage != "All":
            where.append({"field": "stage", "op": "eq", "value": stage})
        if creator:
            user = self.find_user_by_username(creator)
            if not user:
                return [], 0
            where.append({"field": "user_id", "op": "eq", "value": user["id"]})

        orders = {
            "trending": [
                {"field": "likes", "descending": True},
                {"field": "views", "descending": True},
                {"field": "created_at", "descending": True},
            ],
            "newest": [{"field": "created_at", "descending": True}],
            "popular": [
                {"field": "views", "descending": True},
                {"field": "likes", "descending": True},
            ],
        }
        rows, total = self._query(
            PROJECTS, where=where, or_groups=or_groups, order_by=orders[sort],
            limit=limit, offset=(page - 1) * limit,
        )
        liked = self._liked_ids(viewer_id, [row["id"] for row in rows])
        return [self._decorate_project(row, liked) for row in rows], total

    def get_project(self, slug: str, viewer_id=None, increment_views=False):
        rows, _ = self._query(
            PROJECTS, where=[{"field": "slug", "op": "eq", "value": slug}], limit=1
        )
        if not rows:
            return None
        row = rows[0]
        if increment_views:
            row = self._update(PROJECTS, row["id"], inc={"views": 1})
        liked = self._liked_ids(viewer_id, [row["id"]])
        return self._decorate_project(row, liked)

    def create_project(self, fields: dict, media: list[dict], *, is_demo=False):
        now = _now_iso()
        created_at = fields.get("created_at") or now
        data = {
            "slug": fields["slug"],
            "user_id": str(fields["user_id"]),
            "title": fields["title"],
            "tagline": fields["tagline"],
            "description": fields["description"],
            "category": fields["category"],
            "stage": fields["stage"],
            "tags": fields.get("tags") or [],
            "tech": fields.get("tech") or [],
            "repo_url": fields.get("repo_url", ""),
            "demo_url": fields.get("demo_url", ""),
            "website_url": fields.get("website_url", ""),
            "preview_url": fields.get("preview_url", ""),
            "license": fields.get("license", ""),
            "cover_image": fields.get("cover_image", ""),
            "cover_style": fields.get("cover_style", "violet"),
            "is_demo": bool(is_demo or fields.get("is_demo")),
            "created_at": created_at,
            "updated_at": fields.get("updated_at") or created_at,
            "views": int(fields.get("views", 0) or 0),
            "likes": int(fields.get("likes", 0) or 0),
            "creator_name": fields.get("creator_name", ""),
            "creator_username": fields.get("creator_username", ""),
            "creator_color": fields.get("creator_color", "#8b5cf6"),
            "search_text": _search_text(fields),
            "media": media,
        }
        return self._create(
            PROJECTS, data, idempotency_key=f"project-{fields['slug']}"
        )

    def update_project(self, record: dict, fields: dict, media: list[dict]):
        set_fields = {
            "title": fields["title"],
            "tagline": fields["tagline"],
            "description": fields["description"],
            "category": fields["category"],
            "stage": fields["stage"],
            "tags": fields.get("tags") or [],
            "tech": fields.get("tech") or [],
            "repo_url": fields.get("repo_url", ""),
            "demo_url": fields.get("demo_url", ""),
            "website_url": fields.get("website_url", ""),
            "preview_url": fields.get("preview_url", ""),
            "license": fields.get("license", ""),
            "cover_image": fields.get("cover_image", ""),
            "cover_style": fields.get("cover_style", "violet"),
            "updated_at": _now_iso(),
            "search_text": _search_text(fields),
            "media": media,
        }
        return self._update(
            PROJECTS, record["id"], version=record["version"], set_fields=set_fields
        )

    def delete_project(self, record: dict):
        likes, _ = self._all(
            LIKES,
            where=[{"field": "project_id", "op": "eq", "value": record["id"]}],
        )
        for like in likes:
            try:
                self._delete(LIKES, like["id"], like["version"])
            except (RecordNotFound, VersionConflict):
                pass
        return self._delete(PROJECTS, record["id"], record["version"])

    def toggle_like(self, slug: str, user_id):
        with self._like_lock:
            rows, _ = self._query(
                PROJECTS, where=[{"field": "slug", "op": "eq", "value": slug}], limit=1
            )
            if not rows:
                raise RecordNotFound(404, "Project not found")
            project = rows[0]
            key = f"{user_id}:{project['id']}"
            existing, _ = self._query(
                LIKES, where=[{"field": "key", "op": "eq", "value": key}], limit=1
            )
            if existing:
                try:
                    self._delete(LIKES, existing[0]["id"], existing[0]["version"])
                except (RecordNotFound, VersionConflict):
                    pass
                updated = self._update(PROJECTS, project["id"], inc={"likes": -1})
                return False, max(0, int(updated.get("likes", 0) or 0))
            try:
                self._create(
                    LIKES,
                    {
                        "key": key,
                        "user_id": str(user_id),
                        "project_id": project["id"],
                        "created_at": _now_iso(),
                    },
                    # A fresh key per like attempt: the same user may unlike and like
                    # again, and a reused idempotency key would carry the old body.
                    idempotency_key=f"like-{key}-{uuid.uuid4().hex[:12]}",
                )
            except DuplicateRecord:
                current = self._get(PROJECTS, project["id"]) or project
                return True, max(0, int(current.get("likes", 0) or 0))
            updated = self._update(PROJECTS, project["id"], inc={"likes": 1})
            return True, max(0, int(updated.get("likes", 0) or 0))

    def maker_stats(self, username: str):
        user = self.find_user_by_username(username)
        if not user:
            return None
        projects, _ = self._all(
            PROJECTS, where=[{"field": "user_id", "op": "eq", "value": user["id"]}]
        )
        project_ids = [project["id"] for project in projects]
        likes = 0
        for start in range(0, len(project_ids), 500):
            chunk = project_ids[start:start + 500]
            if chunk:
                likes += self._query(
                    LIKES,
                    where=[{"field": "project_id", "op": "in", "value": chunk}],
                    limit=1,
                )[1]
        return {
            "id": user["id"],
            "name": user["name"],
            "username": user["username"],
            "bio": user.get("bio", ""),
            "avatar_color": user.get("avatar_color", "#8b5cf6"),
            "joined_at": user.get("created_at", ""),
            "project_count": len(projects),
            "views": sum(int(project.get("views", 0) or 0) for project in projects),
            "likes": likes,
        }

    # ---------------------------------------------------------------- demo

    def seed_demo(self, demo_entries, slugify):
        """Create the fictional showcase rows once; safe on every boot."""
        if self._query(PROJECTS, limit=1)[1] > 0:
            return
        colors = ["#8b5cf6", "#3b82f6", "#14b8a6", "#f97316"]
        for index, entry in enumerate(demo_entries):
            title, tagline, category, stage, tech, color, description, name = entry
            username = "demo_" + "".join(
                character for character in name.lower() if character.isalnum()
            )
            user = self.find_user_by_username(username)
            if not user:
                try:
                    user = self.create_user(
                        name=name,
                        username=username,
                        email=f"{username}@example.invalid",
                        password_hash="disabled-demo-account",
                        is_demo=True,
                        avatar_color=colors[index % len(colors)],
                    )
                except DuplicateRecord:
                    user = self.find_user_by_username(username)
            if not user:
                continue
            fields = {
                "slug": slugify(title),
                "user_id": user["id"],
                "title": title,
                "tagline": tagline,
                "description": description,
                "category": category,
                "stage": stage,
                "tags": ["Community", "Featured"],
                "tech": [part.strip() for part in tech.split(",") if part.strip()],
                "repo_url": "",
                "demo_url": "",
                "website_url": "",
                "preview_url": "",
                "license": "",
                "cover_image": "",
                "cover_style": color,
                "creator_name": user["name"],
                "creator_username": user["username"],
                "creator_color": user.get("avatar_color", colors[index % len(colors)]),
            }
            try:
                self.create_project(fields, [], is_demo=True)
            except DuplicateRecord:
                pass
