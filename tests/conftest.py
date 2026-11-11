"""Shared fixtures: isolate the SQLite suite and run a fake MemoryDB on demand."""
import pytest

from fake_memorydb import FakeMemoryDB


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Every test starts as a plain SQLite checkout unless it opts into MemoryDB."""
    for name in ("MEMORYDB_URL", "MEMORYDB_TOKEN", "UNIVORYA_STORE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def fake_memorydb():
    server = FakeMemoryDB().start()
    try:
        yield server
    finally:
        server.stop()
