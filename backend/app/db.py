from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .config import settings

SQLITE_BUSY_TIMEOUT_S = 120


@lru_cache
def engine() -> Engine:
    return _make_engine(settings().database_url)


@lru_cache
def _make_engine(database_url: str) -> Engine:
    config = settings()
    if config.database_engine in ("none", "supabase"):
        raise ValueError("Select a direct SQL database first")
    if config.database_engine == "sqlite":
        root = Path(config.data_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        url = make_url(database_url or f"sqlite:///{root / 'application.db'}")
        if url.get_backend_name() != "sqlite" or not url.database:
            raise ValueError("Expected a SQLite file")
        target = Path(url.database).resolve()
        if not target.is_relative_to(root):
            raise ValueError("SQLite files must be inside the project runtime data directory")
        # SQLite has one writer at a time, and an agent may hold the write lock for
        # the length of a model call (seconds, tens of seconds with retries). A
        # second writer must wait for that rather than fail, so the busy timeout
        # is generous. PostgreSQL (the production database) has no such limit.
        sqlite_engine = create_engine(
            url, connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_S}, pool_pre_ping=True
        )

        @event.listens_for(sqlite_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            # WAL lets the embedded worker write while the API reads; foreign keys
            # are off by default in SQLite and the schema relies on them.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_S * 1000}")
            cursor.close()

        return sqlite_engine
    url = make_url(database_url)
    if url.get_backend_name() != config.database_engine:
        raise ValueError("Database engine and URL do not match")
    driver = "postgresql+psycopg" if config.database_engine == "postgresql" else "mysql+pymysql"
    url = url.set(drivername=driver)
    args: dict[str, Any] = {"connect_timeout": 5}
    # A database behind a private or self-signed CA can still be verified, but
    # only if its root certificate is available. Without a way to supply one,
    # verify-full makes those databases permanently unreachable — so the CA is
    # configuration, and verification stays on either way.
    ca_file = config.database_ssl_root_cert or None
    if ca_file and not Path(ca_file).is_file():
        raise ValueError("The configured database CA certificate file does not exist")
    if config.database_engine == "postgresql":
        # verify-full by default. A compose deployment with PostgreSQL on a private
        # network may relax this deliberately via DATABASE_SSL_MODE; production
        # refuses to run without TLS entirely.
        ssl_mode = config.database_ssl_mode
        if config.is_production and ssl_mode in ("disable", "prefer"):
            raise ValueError("Production requires TLS to PostgreSQL (DATABASE_SSL_MODE=require or stronger)")
        # A transaction left open (its request stuck) is ended by the database after a
        # minute, releasing its locks, instead of blocking everyone behind it.
        args.update({"sslmode": ssl_mode, "options": "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000"})
        if ca_file:
            args["sslrootcert"] = ca_file
    else:
        args.update({"ssl_verify_cert": True, "ssl_verify_identity": True,
                     "read_timeout": 10, "write_timeout": 10})
        if ca_file:
            args["ssl_ca"] = ca_file
    return create_engine(url, connect_args=args, pool_size=10, max_overflow=10,
                         pool_timeout=10, pool_pre_ping=True, pool_recycle=300)


@contextmanager
def transaction(*, write: bool = False) -> Iterator[Session]:
    """A session in one transaction. One install is one company: there is one
    connection, used by the admin app, the gateway and the worker alike."""
    if write and settings().database_read_only:
        raise PermissionError("This connection is read-only")
    with Session(engine()) as session, session.begin():
        if settings().database_read_only:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                session.execute(text("SET TRANSACTION READ ONLY"))
            elif dialect == "mysql":
                # MySQL has no per-transaction switch on an already-open
                # transaction, so start a read-only one explicitly rather than
                # leaving this to the credentials and calling it enforced.
                session.execute(text("COMMIT"))
                session.execute(text("START TRANSACTION READ ONLY"))
            elif dialect == "sqlite":
                session.execute(text("PRAGMA query_only = ON"))
        yield session


# The admin app's name for the same transaction.
admin_transaction = transaction
