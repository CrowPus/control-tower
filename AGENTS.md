# Working in this repository

Notes for coding agents. Read [docs/PLAN.md](docs/PLAN.md) (what Control Tower is and the build order) and
[docs/BUILD_LOG.md](docs/BUILD_LOG.md) (what is built, how to build and test) before anything else, and add an entry to
the build log for every finished step.

## Layout

Control Tower is a self-hosted admin and AI-agent office: one install is one company. The owner creates agents from a
template; every agent follows the rules in PLAN §5; projects connect through plugins (PLAN §6).

- `backend/app/` — FastAPI. `main.py` serves the admin (`/admin-api/…` and the built admin app), the agent gateway
  (`/agent-gateway/…`) and the studio's public media links. `worker.py` runs the worker and scheduler.
- `backend/app/services/team/` — the agent engine: registry, runner, tools, clearance (`approvals.py`), budgets, events.
- `backend/app/admin_docs/` — Admin → Docs; every admin page and server function must belong to a section (tested).
- `admin/src/` — the admin app (React 19 + TypeScript + Tailwind). `admin/src/schema.d.ts` is generated.
- SQLite in development, PostgreSQL in production.

## Commands

When the machine runs anything else, run builds and tests only in the memory-capped check container
(`scripts/check-container/`), one heavy job at a time.

```bash
scripts/check-container/run.sh 'cd backend && .venv/bin/pytest -q'          # the backend tests (SQLite)
scripts/check-container/postgres.sh                                          # the same on a throwaway PostgreSQL
scripts/check-container/run.sh 'cd backend && .venv/bin/ruff check app tests && .venv/bin/mypy app'
scripts/check-container/run.sh 'npm run api && npm run build'                # after changing a route: types, then the admin
```

## Invariants

Each of these is load-bearing. A change that weakens one is a defect, however well it reads.

1. **The model proposes; deterministic code acts.** Model output becomes a typed, validated proposal. Nothing an AI
   writes is sent, published or charged without a check and, where the owner set it, the owner's clearance.
2. **The rules in PLAN §5 cannot be switched off**, by a setting, a plugin or an agent's template.
3. **No hardcoding.** Budgets, limits, schedules, texts and every business number are admin settings; the code reads them.
4. **No duplicates at any layer**: tools show what exists, proposals are refused and re-checked, Clearance warns, the
   executor checks, the database has unique keys.
5. **No business in the core.** Anything that belongs to one company (its records, its actions, its numbers) comes
   from a plugin, never from the core.
6. **Rate limits are a safety rail.** Do not raise the defaults.
7. **Never commit secrets.** `.env` is gitignored; keys stay out of fixtures and tests.

## Testing

A change needs a test that fails without it. Backend tests live in `backend/tests/`, run against a fresh database built
from the migrations; they need no network and no API key. `tests/agents_for_tests.py` registers the agents the engine
tests use (the core itself ships only Nia).

## Migrations

`backend/migrations/versions/`. Read what autogenerate writes before committing it: it misses PostgreSQL policies,
triggers and partial indexes.

## Common mistakes

- Reporting a change as working without having run it.
- Sample or illustrative data in views. Displayed values come from the database.
- Changing a route without regenerating the API types.
