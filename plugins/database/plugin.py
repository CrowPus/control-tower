"""Your project's database, read-only: the owner names the questions (SQL queries) agents may ask it; each becomes a
tool, and a query that answers with one number can also be a number in Insights.

Safe by design:
- connect with a database user that may only read (CREATE USER … ; GRANT SELECT …) — the plugin also opens every
  connection read-only and with a time limit, and refuses anything but one SELECT (or WITH … SELECT);
- agents cannot write SQL: they only run the owner's queries, filling in the parameters the owner declared;
- at most 200 rows go back; the columns the owner lists as personal never reach an agent.

The queries are a JSON list in the settings, for example:

    [{"key": "signups", "description": "Sign-ups per day of the last N days.",
      "sql": "select date(created_at) as day, count(*) as signups from users where created_at > now() - make_interval(days => :days) group by 1 order by 1",
      "params": {"days": "integer"}},
     {"key": "users_total", "description": "Users in total.", "sql": "select count(*) from users", "metric": "last"}]

`metric`: "sum" (things that happen each day) or "last" (a total) makes the query's single number a daily number in
Insights; such a query takes no parameters.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.plugin_api import Context, Metric, Param, Plugin, Setting, Tool

# Safety rails, not business values.
MAX_ROWS = 200
TIMEOUT_SECONDS = 10
MAX_QUERIES = 40
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_WRITE = re.compile(r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|lock|vacuum|set|reset|into)\b",
                    re.IGNORECASE)
_engines: dict[str, Any] = {}


def _engine(url: str) -> Any:
    from sqlalchemy import create_engine, event

    if url not in _engines:
        if url.startswith(("postgres://", "postgresql://")):
            url_sa = re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)
            engine = create_engine(url_sa, pool_pre_ping=True, pool_size=2, max_overflow=0,
                                   connect_args={"options": f"-c default_transaction_read_only=on -c statement_timeout={TIMEOUT_SECONDS * 1000}"})
        elif url.startswith("mysql://"):
            engine = create_engine(url.replace("mysql://", "mysql+pymysql://", 1), pool_pre_ping=True, pool_size=2, max_overflow=0,
                                   connect_args={"read_timeout": TIMEOUT_SECONDS + 5})

            @event.listens_for(engine, "connect")
            def _read_only(dbapi_connection: Any, _record: Any) -> None:
                with dbapi_connection.cursor() as cursor:
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute(f"SET SESSION max_execution_time = {TIMEOUT_SECONDS * 1000}")
        elif url.startswith("sqlite:///"):
            # A file on this server, opened read-only.
            engine = create_engine(f"sqlite:///file:{url.removeprefix('sqlite:///')}?mode=ro&uri=true")
        else:
            raise ValueError("The address starts with postgresql://, mysql:// (or sqlite:/// for a file on this server).")
        _engines[url] = engine
    return _engines[url]


def check_sql(sql: str) -> None:
    body = sql.strip().rstrip(";").strip()
    if not _START.match(body):
        raise ValueError("A query starts with SELECT (or WITH … SELECT).")
    if ";" in body:
        raise ValueError("One query only (no ';' inside).")
    if _WRITE.search(re.sub(r"'[^']*'", "''", body)):
        raise ValueError("A query only reads: no INSERT, UPDATE, DELETE, DDL, SET or SELECT … INTO.")


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "(binary)"
    return value


def _run(ctx: Context, sql: str, params: Mapping[str, Any]) -> tuple[list[str], list[list[Any]], bool]:
    from sqlalchemy import text

    url = str(ctx.settings.get("url") or "")
    if not url:
        raise ValueError("The database address is not set (Admin → Plugins → Database).")
    with _engine(url).connect() as connection:
        result = connection.execute(text(sql), dict(params))
        columns = list(result.keys())
        rows = [[_plain(v) for v in row] for row in result.fetchmany(MAX_ROWS + 1)]
        connection.rollback()
    return columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS


def parse(raw: str) -> list[dict[str, Any]]:
    """The owner's queries, checked; ValueError says what is wrong."""
    try:
        queries = json.loads(raw or "[]")
    except ValueError as exc:
        raise ValueError(f"The queries are not valid JSON: {exc}") from exc
    if not isinstance(queries, list) or len(queries) > MAX_QUERIES:
        raise ValueError(f"The queries are a JSON list of at most {MAX_QUERIES}.")
    seen: set[str] = set()
    for q in queries:
        if not isinstance(q, dict) or not _KEY.match(str(q.get("key", ""))) or q["key"] in seen:
            raise ValueError("Each query has a unique key: lowercase letters, digits and _.")
        seen.add(q["key"])
        if not str(q.get("description") or "").strip():
            raise ValueError(f"Query {q['key']}: describe what it answers.")
        check_sql(str(q.get("sql") or ""))
        params = q.get("params") or {}
        if not isinstance(params, dict) or any(t not in ("string", "integer", "number", "boolean") for t in params.values()):
            raise ValueError(f"Query {q['key']}: params map names to string, integer, number or boolean.")
        if q.get("metric") not in (None, "sum", "last"):
            raise ValueError(f"Query {q['key']}: metric is sum or last.")
        if q.get("metric") and params:
            raise ValueError(f"Query {q['key']}: a query that is a number takes no params.")
    return queries


def _tool(q: dict[str, Any]) -> Tool:
    def run(ctx: Context, **args: Any) -> dict[str, Any]:
        columns, rows, more = _run(ctx, q["sql"], args)
        return {"columns": columns, "rows": [dict(zip(columns, r, strict=False)) for r in rows], "more_rows": more}

    return Tool(q["key"], str(q["description"]), run, params={n: Param(t, required=True) for n, t in (q.get("params") or {}).items()},
                view=not q.get("params"))


def _metric(q: dict[str, Any]) -> Metric:
    def read(ctx: Context) -> float:
        _columns, rows, _more = _run(ctx, q["sql"], {})
        if not rows or not rows[0] or not isinstance(rows[0][0], (int, float)):
            raise ValueError("the query must answer with one number")
        return float(rows[0][0])

    return Metric(q["key"], str(q.get("label") or q["key"].replace("_", " ").capitalize()), read, agg=q["metric"], good=q.get("good", "up"))


def build(values: Mapping[str, Any]) -> Plugin:
    queries = parse(str(values.get("queries") or "[]"))
    personal = tuple(p.strip().lower() for p in str(values.get("personal") or "").split(",") if p.strip())
    return _plugin(tuple(_tool(q) for q in queries), tuple(_metric(q) for q in queries if q.get("metric")), personal)


def _plugin(tools: tuple[Tool, ...] = (), metrics: tuple[Metric, ...] = (), personal: tuple[str, ...] = ()) -> Plugin:
    return Plugin(
        key="database",
        name="Database",
        version="1.0.0",
        api="1.0",
        description="Your project's database, read-only: each question you write (an SQL query) becomes a tool agents may use, "
                    "and one that answers with a number can be a number in Insights.",
        settings=(
            Setting("url", "Database address (read-only user)", hint="postgresql://reader:password@host:5432/dbname or mysql://reader:password@host:3306/dbname. "
                    "Use a user that may only SELECT.", secret=True, required=True),
            Setting("queries", "Queries (JSON)", hint='A list like [{"key": "signups", "description": "…", "sql": "select …", "params": {"days": "integer"}}]. '
                    'Add "metric": "sum" or "last" to count a one-number query in Insights. See the plugin\'s file for an example.', required=True, long=True),
            Setting("personal", "Personal columns", hint="Columns agents must never see, comma separated: email, phone, name …"),
        ),
        personal=("email", "phone", "password", "password_hash", *personal),
        tools=tools,
        metrics=metrics,
        build=build,
    )


plugin = _plugin()
