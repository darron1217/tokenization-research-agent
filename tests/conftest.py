from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("EMBED_MODEL", "hash")
os.environ.setdefault("SLACK_WEBHOOK_URL", "")

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path):
    from tokres.db import Database

    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def since():
    return datetime(2026, 9, 1, tzinfo=UTC)


def spec(name: str):
    from tokres.config import load_sources

    return next(s for s in load_sources() if s.name == name)
