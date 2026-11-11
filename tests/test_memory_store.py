"""Unit coverage for the MemoryDB HTTP adapter and its connection strings."""
import pytest

from app.memory_store import (
    USERS,
    DuplicateRecord,
    MemoryDBClient,
    MemoryDBError,
    MemoryStore,
    VersionConflict,
    _search_text,
)
from fake_memorydb import FakeMemoryDB


def test_connection_string_forms(monkeypatch):
    client = MemoryDBClient("memorydb://abc123@example.com:9001")
    assert client.base == "http://example.com:9001/api/v1/"
    assert client.token == "abc123"

    secure = MemoryDBClient("memorydbs://token@db.internal")
    assert secure.base == "https://db.internal:443/api/v1/"
    assert secure.token == "token"

    monkeypatch.setenv("MEMORYDB_TOKEN", "env-token")
    plain = MemoryDBClient("http://127.0.0.1:8090")
    assert plain.base == "http://127.0.0.1:8090/api/v1/"
    assert plain.token == "env-token"

    monkeypatch.delenv("MEMORYDB_TOKEN")
    with pytest.raises(ValueError, match="missing its bearer token"):
        MemoryDBClient("http://127.0.0.1:8090")
    with pytest.raises(ValueError, match="MEMORYDB_URL"):
        MemoryDBClient("postgres://example.com")


def test_wrong_token_maps_to_auth_error(fake_memorydb):
    client = MemoryDBClient(fake_memorydb.url.replace(fake_memorydb.token, "wrong-token-1234567890"))
    with pytest.raises(MemoryDBError) as failure:
        client.health()
    assert failure.value.status == 401


def test_versions_duplicates_expiry_and_sessions(fake_memorydb):
    store = MemoryStore(fake_memorydb.url).initialize(attempts=1)
    user = store.create_user(
        name="Alice Maker", username="alice", email="alice@example.com",
        password_hash="hashed-password",
    )
    assert user["username"] == "alice"
    assert store.find_user_by_username("ALICE")["id"] == user["id"]
    assert store.find_user_by_identifier("ALICE@EXAMPLE.COM")["id"] == user["id"]

    with pytest.raises(DuplicateRecord):
        store.create_user(
            name="Impostor", username="alice", email="other@example.com",
            password_hash="hashed-password",
        )
    with pytest.raises(DuplicateRecord):
        store.create_user(
            name="Impostor", username="other", email="ALICE@example.com",
            password_hash="hashed-password",
        )

    updated = store._update(USERS, user["id"], version=user["version"], set_fields={"bio": "new bio"})
    assert updated["version"] == user["version"] + 1
    assert updated["bio"] == "new bio"
    with pytest.raises(VersionConflict):
        store._update(USERS, user["id"], version=user["version"], set_fields={"bio": "stale"})
    with pytest.raises(VersionConflict):
        store._delete(USERS, user["id"], user["version"])

    expired = store.create_session("expired-hash", user["id"], -1)
    assert expired["token_hash"] == "expired-hash"
    assert store.user_for_session("expired-hash") is None

    store.create_session("live-hash", user["id"], 3600)
    assert store.user_for_session("live-hash")["username"] == "alice"
    store.delete_session("live-hash")
    assert store.user_for_session("live-hash") is None

    assert store.get_user("missing-id") is None
    assert store.find_user_by_username("ghost") is None


def test_search_text_joins_every_searchable_field():
    text = _search_text({
        "title": "Nightowl", "tagline": "Read together",
        "description": "Live annotations",
        "tags": ["reading", "open source"], "tech": ["FastAPI", "SQLite"],
    })
    for piece in ("Nightowl", "Read together", "Live annotations", "reading", "open source", "FastAPI"):
        assert piece in text


def test_initialize_retries_then_fails_without_a_server():
    with pytest.raises(RuntimeError, match="not ready"):
        MemoryStore("memorydb://token-1234567890@127.0.0.1:1").initialize(attempts=1)
