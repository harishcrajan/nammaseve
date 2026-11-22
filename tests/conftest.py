"""Shared fixtures: force mock mode and give every test a fresh SQLite file."""
import os
import sys
from pathlib import Path

os.environ["MOCK_MODE"] = "true"
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WF_DB_PATH", str(tmp_path / "test.db"))
    from db.database import init_db
    init_db()
    yield
