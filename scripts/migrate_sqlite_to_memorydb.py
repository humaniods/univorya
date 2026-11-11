"""Copy an existing Univorya SQLite database into MemoryDB.

Run this once, with the Univorya app stopped, after taking a backup:

    python scripts/migrate_sqlite_to_memorydb.py \
        --sqlite data/univorya.sqlite3 \
        --memorydb http://127.0.0.1:8080

``MEMORYDB_TOKEN`` supplies the bearer token for ``http(s)://`` URLs, or pass
``memorydb://TOKEN@host:port``.  Users, projects, embedded media metadata and
likes are copied; sessions are deliberately not, because a migration should not
carry live login cookies across deployments.  Re-running the script is safe:
rows are matched by unique username, slug and (user, project) like key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.memory_store import (  # noqa: E402
    LIKES,
    PROJECTS,
    DuplicateRecord,
    MemoryStore,
    VersionConflict,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_list(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def truthy(value) -> bool:
    return bool(int(value)) if isinstance(value, (int, float)) else bool(value)


def table_rows(con: sqlite3.Connection, table: str) -> list[dict]:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return []
    return [dict(row) for row in con.execute(f"SELECT * FROM {table}")]


def redact(connection_string: str) -> str:
    return re.sub(r"//[^@/]+@", "//***@", connection_string)


def migrate(sqlite_path: Path, store: MemoryStore, *, dry_run: bool = False) -> dict:
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")

    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        users = table_rows(con, "users")
        projects = table_rows(con, "projects")
        media = table_rows(con, "project_media")
        likes = table_rows(con, "likes")
    finally:
        con.close()

    if not users and not projects:
        raise SystemExit(f"{sqlite_path} has no Univorya users or projects to migrate")

    report = {
        "users_created": 0, "users_present": 0,
        "projects_created": 0, "projects_present": 0,
        "media_embedded": 0, "likes_created": 0, "likes_present": 0,
        "skipped_likes": 0, "skipped_projects": 0,
    }
    if dry_run:
        report.update(source_users=len(users), source_projects=len(projects),
                      source_media=len(media), source_likes=len(likes))
        return report

    media_by_project: dict[int, list[dict]] = {}
    for row in media:
        media_by_project.setdefault(row["project_id"], []).append({
            "id": str(row["id"]),
            "kind": row.get("kind") or "screenshot",
            "file": row.get("file") or "",
            "width": int(row.get("width") or 0),
            "height": int(row.get("height") or 0),
            "bytes": int(row.get("bytes") or 0),
            "animated": truthy(row.get("animated")),
            "position": int(row.get("position") or 0),
            "uploaded_at": row.get("created_at") or utcnow(),
        })
    for items in media_by_project.values():
        items.sort(key=lambda item: (item["position"], item["id"]))

    # Demo accounts are not flagged on SQLite; infer them from the demo_ prefix or
    # from owning only fictional projects, so community counters stay meaningful.
    projects_by_user: dict[int, list[dict]] = {}
    for project in projects:
        projects_by_user.setdefault(project["user_id"], []).append(project)
    demo_users = {
        user["id"] for user in users
        if str(user.get("username", "")).lower().startswith("demo_")
        or (projects_by_user.get(user["id"])
            and all(truthy(project.get("is_demo")) for project in projects_by_user[user["id"]]))
    }

    user_ids: dict[int, str] = {}
    for row in users:
        existing = store.find_user_by_username(row["username"])
        if existing:
            user_ids[row["id"]] = existing["id"]
            report["users_present"] += 1
            continue
        try:
            user = store.create_user(
                name=row["name"],
                username=row["username"],
                email=row["email"],
                password_hash=row["password_hash"],
                is_demo=row["id"] in demo_users,
                bio=row.get("bio") or "",
                avatar_color=row.get("avatar_color") or "#8b5cf6",
                created_at=row.get("created_at"),
            )
            user_ids[row["id"]] = user["id"]
            report["users_created"] += 1
        except DuplicateRecord as exc:
            raise SystemExit(
                f"Cannot migrate user {row['username']!r}: {exc}. "
                "A different account already owns that username or email."
            ) from exc

    project_ids: dict[int, str] = {}
    for row in projects:
        owner_id = user_ids.get(row["user_id"])
        if not owner_id:
            report["skipped_projects"] += 1
            continue
        owner = store.get_user(owner_id)
        existing = store.get_project(row["slug"])
        if existing:
            project_ids[row["id"]] = existing["id"]
            report["projects_present"] += 1
            continue
        items = media_by_project.get(row["id"], [])
        fields = {
            "slug": row["slug"],
            "user_id": owner_id,
            "title": row["title"],
            "tagline": row["tagline"],
            "description": row["description"],
            "category": row["category"],
            "stage": row["stage"],
            "tags": json_list(row.get("tags")),
            "tech": json_list(row.get("tech")),
            "repo_url": row.get("repo_url") or "",
            "demo_url": row.get("demo_url") or "",
            "website_url": row.get("website_url") or "",
            "preview_url": row.get("preview_url") or "",
            "license": row.get("license") or "",
            "cover_image": row.get("cover_image") or "",
            "cover_style": row.get("cover_style") or "violet",
            "is_demo": truthy(row.get("is_demo")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "views": int(row.get("views") or 0),
            "creator_name": owner.get("name", ""),
            "creator_username": owner.get("username", ""),
            "creator_color": owner.get("avatar_color") or "#8b5cf6",
        }
        try:
            project = store.create_project(fields, items, is_demo=truthy(row.get("is_demo")))
        except DuplicateRecord as exc:
            raise SystemExit(
                f"Cannot migrate project {row['slug']!r}: {exc}. "
                "Another project already owns that slug."
            ) from exc
        project_ids[row["id"]] = project["id"]
        report["projects_created"] += 1
        report["media_embedded"] += len(items)

    for row in likes:
        user_id = user_ids.get(row["user_id"])
        project_id = project_ids.get(row["project_id"])
        if not user_id or not project_id:
            report["skipped_likes"] += 1
            continue
        key = f"{user_id}:{project_id}"
        existing, _ = store._query(
            LIKES, where=[{"field": "key", "op": "eq", "value": key}], limit=1
        )
        if existing:
            report["likes_present"] += 1
            continue
        store._create(
            LIKES,
            {"key": key, "user_id": user_id, "project_id": project_id,
             "created_at": utcnow()},
            idempotency_key=f"like-migrate-{key}",
        )
        report["likes_created"] += 1

    # Recompute the denormalised counter from the like documents that actually
    # exist.  This also repairs a run that stopped between the like and counter
    # writes, and makes a second migration converge instead of double-counting.
    for project_id in set(project_ids.values()):
        live = store._get(PROJECTS, project_id)
        if not live:
            continue
        _, actual = store._query(
            LIKES, where=[{"field": "project_id", "op": "eq", "value": project_id}], limit=1
        )
        if int(live.get("likes", 0) or 0) != actual:
            try:
                store._update(PROJECTS, project_id, version=live["version"],
                              set_fields={"likes": actual})
            except VersionConflict:
                pass

    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Copy a Univorya SQLite database into MemoryDB.")
    parser.add_argument(
        "--sqlite",
        default=str(Path(os.environ.get("UNIVORYA_DATA_DIR", ROOT / "data")) / "univorya.sqlite3"),
        help="path to univorya.sqlite3 (default: $UNIVORYA_DATA_DIR/univorya.sqlite3)",
    )
    parser.add_argument(
        "--memorydb",
        default=os.environ.get("MEMORYDB_URL", ""),
        help="memorydb://TOKEN@host:port or http://host:port (default: $MEMORYDB_URL)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report source counts only")
    args = parser.parse_args(argv)

    sqlite_path = Path(args.sqlite)
    if not args.memorydb:
        return _fail("Set MEMORYDB_URL or pass --memorydb; the target must be a running MemoryDB.")

    print(f"Source:   {sqlite_path}")
    print(f"MemoryDB: {redact(args.memorydb)}")
    try:
        store = MemoryStore(args.memorydb).initialize(attempts=10)
    except Exception as exc:  # clear message instead of a traceback for operators
        return _fail(f"MemoryDB is not reachable: {exc}")

    try:
        report = migrate(sqlite_path, store, dry_run=args.dry_run)
    except SystemExit as exc:
        return _fail(str(exc))

    if args.dry_run:
        print("Dry run; nothing was written.")
        print(f"Would read {report['source_users']} users, {report['source_projects']} projects, "
              f"{report['source_media']} media rows and {report['source_likes']} likes.")
        return 0

    print(f"Users:    {report['users_created']} created, {report['users_present']} already present")
    print(f"Projects: {report['projects_created']} created, {report['projects_present']} already present")
    print(f"Media:    {report['media_embedded']} metadata rows embedded")
    print(f"Likes:    {report['likes_created']} created, {report['likes_present']} already present")
    if report["skipped_projects"] or report["skipped_likes"]:
        print(f"Skipped:  {report['skipped_projects']} projects and "
              f"{report['skipped_likes']} likes referencing missing rows")
    print("Migration complete. Start Univorya with UNIVORYA_STORE=memorydb.")
    return 0


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
