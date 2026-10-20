"""Bring the database up to date before the server starts.

A deployed container has nobody to do it, so without this step a fresh install
starts with no tables and every request that touches data fails.

Only a database this install owns (`DATABASE_OWNERSHIP=application`: its own
SQLite file, or the PostgreSQL the compose file provisions for it) is migrated
here. A database somebody else owns is never changed on startup: run
`alembic upgrade head` against it on purpose, when you mean to.
"""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import settings
from .db import engine


def owns_database() -> bool:
    return settings().database_ownership == "application"


def main() -> None:
    if not owns_database():
        print("prestart: not this install's own database; leaving the schema alone")
        return
    config = settings()
    if config.database_migration_url:
        os.environ["DATABASE_URL"] = config.database_migration_url
        settings.cache_clear()
        engine.cache_clear()
    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    command.upgrade(Config(str(alembic_ini)), "head")
    print("prestart: database is up to date")


if __name__ == "__main__":
    main()
