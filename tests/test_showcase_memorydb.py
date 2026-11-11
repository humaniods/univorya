"""The showcase launcher must read the same production store as the web app."""
import importlib.util
from pathlib import Path

from app.memory_store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("showcase", ROOT / "scripts" / "showcase.py")
showcase = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(showcase)


def seed_project(store):
    user = store.create_user(
        name="Alice Maker", username="alice", email="alice@example.com",
        password_hash="hash", avatar_color="#123456",
    )
    store.create_project({
        "slug": "nightowl-abc123",
        "user_id": user["id"],
        "title": "Nightowl Reader",
        "tagline": "Read together in a calm place.",
        "description": "A description long enough for the showcase fixture to be realistic.",
        "category": "Web App",
        "stage": "Beta",
        "tags": ["reading"],
        "tech": ["FastAPI"],
        "preview_url": "http://localhost:5173",
        "creator_name": user["name"],
        "creator_username": user["username"],
        "creator_color": user["avatar_color"],
    }, [])


def test_showcase_prints_a_memorydb_preview(fake_memorydb, monkeypatch, capsys):
    store = MemoryStore(fake_memorydb.url).initialize(attempts=1)
    seed_project(store)
    monkeypatch.setenv("MEMORYDB_URL", fake_memorydb.url)
    monkeypatch.setenv("UNIVORYA_STORE", "memorydb")

    assert showcase.main(["nightowl-abc123", "--print"]) == 0
    assert capsys.readouterr().out.strip() == "http://localhost:5173"


def test_showcase_reports_a_missing_memorydb_project(fake_memorydb, monkeypatch, capsys):
    MemoryStore(fake_memorydb.url).initialize(attempts=1)
    monkeypatch.setenv("MEMORYDB_URL", fake_memorydb.url)
    monkeypatch.setenv("UNIVORYA_STORE", "memorydb")

    assert showcase.main(["not-a-project", "--print"]) == 1
    assert 'No project with the slug "not-a-project"' in capsys.readouterr().err
