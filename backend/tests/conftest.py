"""Test environment.

Configuration is read once per process, so the environment is fixed *before*
the application is imported: test mode, an isolated data directory, no model,
no mail server and no worker thread. The schema comes from the Alembic
migrations (not `create_all`), so every test run also proves the migrations
build a fresh database.

TEST_DATABASE_URL=postgresql://user:pw@host:port/db runs the whole suite on
PostgreSQL (scripts/check-container/postgres.sh starts a throwaway one).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

_DATA_DIR = Path(tempfile.mkdtemp(prefix="ct-test-"))
os.environ.update(
    {
        "EXECUTION_MODE": "test",
        "DATA_DIR": str(_DATA_DIR),
        "MARKETING_DIR": str(_DATA_DIR / "marketing"),
        "DATABASE_ENGINE": "sqlite",
        "DATABASE_URL": "",
        "DATABASE_READ_ONLY": "false",
        "SUPPORT_EMAIL": "support@tower.example",
        "PRIVACY_URL": "https://tower.example/privacy",
        "ADMIN_ALERT_EMAIL": "owner@tower.example",
        "ENCRYPTION_SECRET": "test-secret-key-for-the-suite-0123456789",
        "GEMINI_API_KEY": "",
        "SMTP_HOST": "",
        "SMTP_PORT": "587",
        "SMTP_USER": "",
        "SMTP_PASSWORD": "",
        "SMTP_FROM": "",
        "EMBEDDED_WORKER": "false",
        "EMBEDDED_SCHEDULER": "false",
        "REDIS_URL": "",
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
    }
)

_PG = os.environ.get("TEST_DATABASE_URL", "")
if _PG:
    os.environ.update({"DATABASE_ENGINE": "postgresql", "DATABASE_URL": _PG, "DATABASE_SSL_MODE": "disable"})

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.cache_clear()
command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")

from app.ai import reset_for_tests  # noqa: E402
from app.main import app  # noqa: E402
from tests import agents_for_tests  # noqa: E402

agents_for_tests.register()

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _cleanup() -> Iterator[None]:
    reset_for_tests()
    yield
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


@pytest.fixture
def anon() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def run_queued(task_type: str) -> list[dict[str, object]]:
    """Run the queued tasks of one type, as the worker would, and mark them done."""
    from sqlalchemy import select

    from app.db import transaction
    from app.models import AgentTask
    from app.workers.worker import handlers

    with transaction() as db:
        rows = db.execute(select(AgentTask.id, AgentTask.queue, AgentTask.payload).where(AgentTask.task_type == task_type, AgentTask.status == "queued")).all()
    results: list[dict[str, object]] = []
    for task_id, queue, payload in rows:
        results.append(handlers()[task_type](AgentTask(id=task_id, queue=queue, task_type=task_type, payload=dict(payload), status="running")) or {})
        with transaction(write=True) as db:
            db.get(AgentTask, task_id).status = "succeeded"
    return results
