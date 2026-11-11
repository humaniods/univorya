"""The SQLite → MemoryDB migration must preserve data and be safe to re-run."""
import importlib.util
import sqlite3
from pathlib import Path

from app.memory_store import LIKES, PROJECTS, USERS, MemoryStore

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "migrate_sqlite_to_memorydb", ROOT / "scripts" / "migrate_sqlite_to_memorydb.py"
)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def make_sqlite(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL, bio TEXT DEFAULT '',
          avatar_color TEXT DEFAULT '#8b5cf6', created_at TEXT NOT NULL);
        CREATE TABLE projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE,
          user_id INTEGER NOT NULL, title TEXT NOT NULL, tagline TEXT NOT NULL,
          description TEXT NOT NULL, category TEXT NOT NULL, stage TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT '[]', tech TEXT NOT NULL DEFAULT '[]',
          repo_url TEXT DEFAULT '', demo_url TEXT DEFAULT '', website_url TEXT DEFAULT '',
          preview_url TEXT NOT NULL DEFAULT '', license TEXT DEFAULT '',
          cover_image TEXT DEFAULT '', cover_style TEXT DEFAULT 'violet',
          is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, views INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE likes (user_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
          PRIMARY KEY(user_id, project_id));
        CREATE TABLE project_media (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          kind TEXT NOT NULL DEFAULT 'screenshot', file TEXT NOT NULL,
          width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
          bytes INTEGER NOT NULL DEFAULT 0, animated INTEGER NOT NULL DEFAULT 0,
          position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    """)
    con.execute(
        "INSERT INTO users(id,name,username,email,password_hash,bio,avatar_color,created_at) "
        "VALUES(1,'Alice Maker','alice','alice@example.com','hash-one','Bio','#123456',"
        "'2025-01-02T03:04:05+00:00')"
    )
    con.execute(
        "INSERT INTO users(id,name,username,email,password_hash,bio,avatar_color,created_at) "
        "VALUES(2,'Lena Torres','demo_lenatorres','demo_lenatorres@example.invalid','demo',"
        "'', '#8b5cf6','2025-01-03T03:04:05+00:00')"
    )
    con.execute(
        "INSERT INTO projects(id,slug,user_id,title,tagline,description,category,stage,tags,"
        "tech,repo_url,demo_url,website_url,preview_url,license,cover_image,cover_style,is_demo,"
        "created_at,updated_at,views) VALUES(1,'nightowl-abc123',1,'Nightowl',"
        "'Read together in a calm place.','Long enough description for the migration fixture.',"
        "'Web App','Beta','[\"reading\",\"open source\"]','[\"FastAPI\",\"SQLite\"]',"
        "'https://github.com/example/nightowl','','','http://localhost:5173','MIT',"
        "'/uploads/cover.webp','blue',0,'2025-02-01T10:00:00+00:00',"
        "'2025-02-02T10:00:00+00:00',7)"
    )
    con.execute(
        "INSERT INTO projects(id,slug,user_id,title,tagline,description,category,stage,tags,"
        "tech,repo_url,demo_url,website_url,preview_url,license,cover_image,cover_style,is_demo,"
        "created_at,updated_at,views) VALUES(2,'untitled-ui-def456',2,'Untitled UI',"
        "'A fictional demo project.','Short demo description that still satisfies the fixture.',"
        "'Design','Launched','[\"Community\"]','[\"Figma\"]','','','','','','','violet',1,"
        "'2025-01-03T10:00:00+00:00','2025-01-03T10:00:00+00:00',3)"
    )
    con.execute(
        "INSERT INTO project_media(id,project_id,kind,file,width,height,bytes,animated,position,"
        "created_at) VALUES(1,1,'cover','/uploads/cover.webp',600,400,1234,0,0,"
        "'2025-02-01T10:01:00+00:00')"
    )
    con.execute(
        "INSERT INTO project_media(id,project_id,kind,file,width,height,bytes,animated,position,"
        "created_at) VALUES(2,1,'screenshot','/uploads/shot.webp',700,500,2345,1,0,"
        "'2025-02-01T10:02:00+00:00')"
    )
    con.execute("INSERT INTO likes(user_id,project_id) VALUES(2,1)")
    con.commit()
    con.close()


def test_migration_preserves_users_projects_media_and_likes(fake_memorydb, tmp_path):
    database = tmp_path / "univorya.sqlite3"
    make_sqlite(database)

    assert migration.main(["--sqlite", str(database), "--memorydb", fake_memorydb.url]) == 0

    store = MemoryStore(fake_memorydb.url)
    alice = store.find_user_by_username("alice")
    assert alice["bio"] == "Bio"
    assert alice["avatar_color"] == "#123456"
    assert alice["created_at"] == "2025-01-02T03:04:05+00:00"
    assert alice["is_demo"] is False

    demo = store.find_user_by_username("demo_lenatorres")
    assert demo["is_demo"] is True, "an account that only owns demo projects is a demo account"

    project = store.get_project("nightowl-abc123")
    assert project["user_id"] == alice["id"]
    assert project["views"] == 7
    assert project["likes"] == 1
    assert project["preview_url"] == "http://localhost:5173"
    assert project["created_at"] == "2025-02-01T10:00:00+00:00"
    assert project["tags"] == ["reading", "open source"]
    assert project["tech"] == ["FastAPI", "SQLite"]
    assert project["creator_username"] == "alice"
    assert [item["file"] for item in project["media"]] == [
        "/uploads/cover.webp", "/uploads/shot.webp"
    ]
    assert project["media"][1]["animated"] is True
    assert project["media"][1]["width"] == 700

    demo_project = store.get_project("untitled-ui-def456")
    assert demo_project["is_demo"] is True
    assert demo_project["user_id"] == demo["id"]

    assert store._query(USERS, limit=1)[1] == 2
    assert store._query(PROJECTS, limit=1)[1] == 2
    assert store._query(LIKES, limit=1)[1] == 1

    # Re-running matches by username/slug/like key and writes nothing twice.
    second = migration.migrate(database, store)
    assert second == {
        "users_created": 0, "users_present": 2,
        "projects_created": 0, "projects_present": 2,
        "media_embedded": 0, "likes_created": 0, "likes_present": 1,
        "skipped_likes": 0, "skipped_projects": 0,
    }
    assert store.get_project("nightowl-abc123")["likes"] == 1
    assert store._query(USERS, limit=1)[1] == 2
    assert store._query(PROJECTS, limit=1)[1] == 2
    assert store._query(LIKES, limit=1)[1] == 1

    dry = migration.migrate(database, store, dry_run=True)
    assert dry["source_users"] == 2
    assert dry["source_projects"] == 2
    assert dry["source_media"] == 2
    assert dry["source_likes"] == 1


def test_missing_database_is_a_clean_failure(fake_memorydb, tmp_path):
    assert migration.main([
        "--sqlite", str(tmp_path / "absent.sqlite3"), "--memorydb", fake_memorydb.url,
    ]) == 1
