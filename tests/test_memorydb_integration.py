"""End-to-end coverage for the MemoryDB production store.

The suite drives the real FastAPI app over HTTP against an in-process fake of the
MemoryDB API, so every adapter branch (auth, projects, media, likes, search,
editing, deletion and demo seeding) is exercised through the same wire format the
Linux deployment uses.
"""
import importlib
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def png(width=620, height=360, color=(115, 86, 220)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def project_form(**overrides):
    fields = {
        "title": "Nightowl Reading App",
        "tagline": "Read, annotate and discover stories together.",
        "description": "A community-powered reading application with live annotations, "
                       "customizable bookmarks and a peaceful reading experience.",
        "category": "Web App",
        "stage": "Beta",
        "tags": "reading, open source",
        "tech": "Python, FastAPI",
        "repo_url": "https://github.com/example/nightowl",
        "demo_url": "",
        "website_url": "",
        "license": "MIT",
        "cover_style": "blue",
    }
    fields.update(overrides)
    return fields


def signup(client, username="alice", email=None, name="Alice Maker"):
    return client.post("/api/auth/register", json={
        "name": name,
        "username": username,
        "email": email or f"{username}@example.com",
        "password": "Correct-Horse-59",
    })


def login(client, identifier="alice", password="Correct-Horse-59"):
    return client.post("/api/auth/login", json={"email": identifier, "password": password})


def publish(client, cover=True, shots=2, **overrides):
    files = []
    if cover:
        files.append(("cover", ("cover.png", png(), "image/png")))
    for index in range(shots):
        files.append(
            ("screenshots", (f"shot{index}.png", png(700, 500, (index * 60, 120, 200)), "image/png"))
        )
    kwargs = {"data": project_form(**overrides)}
    if files:
        kwargs["files"] = files
    return client.post("/api/projects", **kwargs)


@pytest.fixture()
def memory(tmp_path, monkeypatch, fake_memorydb):
    monkeypatch.setenv("UNIVORYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "0")
    monkeypatch.setenv("UNIVORYA_STORE", "memorydb")
    monkeypatch.setenv("MEMORYDB_URL", fake_memorydb.url)
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        client.fake = fake_memorydb
        client.main = main
        client.data_dir = tmp_path
        yield client


def test_account_lifecycle_and_session_store(memory):
    client = memory
    assert client.get("/api/health").json() == {
        "status": "ok", "service": "univorya", "store": "memorydb",
    }

    created = signup(client)
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    assert user["username"] == "alice"
    assert isinstance(user["id"], str) and user["id"]

    assert client.get("/api/auth/me").json()["user"]["username"] == "alice"
    assert signup(client, email="alice@example.com", name="Other Alice").status_code == 409
    assert signup(client, username="alice").status_code == 409

    assert login(client, password="wrong-password").status_code == 401
    assert login(client, "Alice@Example.com", "Correct-Horse-59").status_code == 200

    # Sessions live in MemoryDB with a real expiry, not in the local database.
    sessions = client.fake._active("univorya_sessions")
    assert len(sessions) == 2
    assert all(record["data"]["user_id"] == user["id"] for record in sessions)
    assert all(record.get("expires") for record in sessions)

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None


def test_project_media_edit_like_and_delete(memory):
    client = memory
    assert signup(client).status_code == 201

    created = publish(client)
    assert created.status_code == 201, created.text
    slug = created.json()["slug"]

    detail = client.get(f"/api/projects/{slug}").json()["project"]
    assert detail["title"] == "Nightowl Reading App"
    assert detail["views"] == 1
    assert detail["media_count"] == 3
    assert [item["kind"] for item in detail["media"]] == ["cover", "screenshot", "screenshot"]
    assert detail["media"][1]["width"] == 700
    assert detail["is_owner"] is True
    assert detail["creator"] == {"name": "Alice Maker", "username": "alice",
                                 "avatar_color": detail["creator"]["avatar_color"]}
    cover_file = client.data_dir / "uploads" / detail["cover_image"].rsplit("/", 1)[-1]
    assert cover_file.is_file()

    listing = client.get("/api/projects").json()
    assert listing["pagination"] == {"page": 1, "limit": 24, "total": 1, "pages": 1}
    assert listing["projects"][0]["media_count"] == 3
    assert listing["projects"][0]["liked"] is False
    assert client.get("/api/metadata").json()["community"] == {"makers": 1, "projects": 1}

    maker = client.get("/api/makers/alice").json()["maker"]
    assert maker["project_count"] == 1
    assert maker["views"] == 1
    assert maker["likes"] == 0
    assert client.get("/api/makers/nobody").status_code == 404

    liked = client.post(f"/api/projects/{slug}/like").json()
    assert liked == {"liked": True, "likes": 1}
    assert client.get(f"/api/projects/{slug}").json()["project"]["liked"] is True
    assert client.post(f"/api/projects/{slug}/like").json() == {"liked": False, "likes": 0}

    # Liking again must create a fresh document; reusing an idempotency key from the
    # first like would make MemoryDB reject the new body as a key conflict.
    assert client.post(f"/api/projects/{slug}/like").json() == {"liked": True, "likes": 1}
    assert client.post(f"/api/projects/{slug}/like").json() == {"liked": False, "likes": 0}

    # A second account has its own like, and the denormalised counter follows.
    other = TestClient(client.app)
    assert signup(other, "bob", "bob@example.com", "Bob Builder").status_code == 201
    assert other.post(f"/api/projects/{slug}/like").json() == {"liked": True, "likes": 1}
    assert client.get(f"/api/projects/{slug}").json()["project"]["liked"] is False
    assert client.get(f"/api/projects/{slug}").json()["project"]["likes"] == 1

    # Edit text, keep both screenshots and replace the cover.
    screenshot_ids = [item["id"] for item in detail["media"] if item["kind"] == "screenshot"]
    edited = client.put(f"/api/projects/{slug}", data=project_form(
        title="Nightowl Reader 2",
        tagline="The reading app now with a sharper, brighter editor.",
        keep_media=",".join(screenshot_ids),
    ), files=[("cover", ("new-cover.png", png(640, 400, (30, 90, 200)), "image/png"))])
    assert edited.status_code == 200, edited.text
    assert edited.json()["slug"] == slug

    updated = client.get(f"/api/projects/{slug}").json()["project"]
    assert updated["title"] == "Nightowl Reader 2"
    assert updated["media_count"] == 3
    assert updated["cover_image"] != detail["cover_image"]
    assert not cover_file.exists(), "replaced cover should be removed from disk"
    assert [item["id"] for item in updated["media"] if item["kind"] == "screenshot"] == screenshot_ids
    assert [item["position"] for item in updated["media"] if item["kind"] == "screenshot"] == [0, 1]

    # A non-owner cannot edit or delete, and demo rows are not editable.
    assert other.put(f"/api/projects/{slug}", data=project_form()).status_code == 403
    assert other.delete(f"/api/projects/{slug}").status_code == 403

    # Deleting removes the project, its likes and every uploaded file.
    media_files = [item["file"].rsplit("/", 1)[-1] for item in updated["media"]]
    assert client.delete(f"/api/projects/{slug}").status_code == 200
    assert client.get(f"/api/projects/{slug}").status_code == 404
    assert client.get("/api/projects").json()["pagination"]["total"] == 0
    assert client.fake._active("univorya_likes") == []
    assert all(not (client.data_dir / "uploads" / name).exists() for name in media_files)
    assert client.get("/api/metadata").json()["community"] == {"makers": 2, "projects": 0}


def test_search_filters_sorting_and_pagination(memory):
    client = memory
    assert signup(client).status_code == 201
    for title, category, stage in (
        ("Nightowl Reader", "Web App", "Beta"),
        ("Orbit Notes", "AI & ML", "Launched"),
        ("Pixel Garden", "Design", "Idea"),
    ):
        assert publish(client, title=title, category=category, stage=stage).status_code == 201

    found = client.get("/api/projects", params={"q": "orbit"}).json()
    assert [project["title"] for project in found["projects"]] == ["Orbit Notes"]

    assert client.get("/api/projects", params={"q": "annotations"}).json()["projects"], \
        "description search must work through the joined search_text field"

    designs = client.get("/api/projects", params={"category": "Design"}).json()
    assert [project["title"] for project in designs["projects"]] == ["Pixel Garden"]

    ideas = client.get("/api/projects", params={"stage": "Idea"}).json()
    assert [project["title"] for project in ideas["projects"]] == ["Pixel Garden"]

    by_creator = client.get("/api/projects", params={"creator": "alice"}).json()
    assert by_creator["pagination"]["total"] == 3
    assert client.get("/api/projects", params={"creator": "ghost"}).json()["pagination"]["total"] == 0

    newest = client.get("/api/projects", params={"sort": "newest"}).json()["projects"]
    assert newest[0]["title"] == "Pixel Garden"

    page = client.get("/api/projects", params={"limit": 2, "page": 2}).json()
    assert len(page["projects"]) == 1
    assert page["pagination"] == {"page": 2, "limit": 2, "total": 3, "pages": 2}

    for params in ({"category": "Nope"}, {"stage": "Nope"}, {"sort": "nope"},
                   {"limit": 0}, {"page": 0}):
        assert client.get("/api/projects", params=params).status_code == 422


def test_demo_seeding_is_idempotent(memory, monkeypatch):
    client = memory
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "1")
    client.main.seed_demo()
    client.main.seed_demo()

    listing = client.get("/api/projects", params={"limit": 60}).json()
    assert listing["pagination"]["total"] == 8
    assert all(project["is_demo"] for project in listing["projects"])
    assert all(project["creator"]["username"].startswith("demo_") for project in listing["projects"])
    assert client.get("/api/metadata").json()["community"] == {"makers": 0, "projects": 0}
    assert client.get("/api/projects", params={"creator": "demo_lenatorres"}).json()[
        "pagination"]["total"] == 2
    assert client.get("/api/makers/demo_lenatorres").json()["maker"]["project_count"] == 2


def test_memorydb_outage_degrades_health_and_api(fake_memorydb, tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVORYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "0")
    monkeypatch.setenv("UNIVORYA_STORE", "memorydb")
    monkeypatch.setenv("MEMORYDB_URL", fake_memorydb.url)
    from app import main
    importlib.reload(main)
    fake_memorydb.stop()
    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert client.get("/api/projects").status_code == 503
