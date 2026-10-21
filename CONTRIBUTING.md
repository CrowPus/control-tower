# Contributing to Control Tower

Thanks for being here. This guide gets you from clone to a merged change without guessing. Coding agents: read
[AGENTS.md](AGENTS.md) too — it lists the invariants a change must keep.

## Ground rules

- **Discuss before you build something large.** Open an issue or a [discussion](https://github.com/CrowPus/control-tower/discussions)
  first for anything that changes the plugin interface, the agent rules, a database table, or takes more than a day.
  Small fixes need no ceremony — just send the pull request.
- **One concern per pull request.** A bug fix and a refactor in the same diff is two pull requests.
- **`main` stays releasable.** CI must be green; when you change behaviour, change the docs in the same pull request.
- **The rules are not optional.** Agents only propose; the owner clears; budgets hold; personal fields never reach an
  agent; every business number is a setting. A change that weakens one of these is a defect, however well it reads
  (see [AGENTS.md → Invariants](AGENTS.md#invariants)).

## Development setup

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22 and npm.

```bash
git clone https://github.com/CrowPus/control-tower.git
cd control-tower
cd backend && uv sync --all-extras && cd ..
npm install
cp .env.example .env   # then set EXECUTION_MODE=development, DATABASE_ENGINE=sqlite and an ENCRYPTION_SECRET
cd backend
uv run python -m app.prestart                                                     # the database (SQLite)
uv run python -m app.admin_cli create-owner --email you@example.com --name "You"  # prints your sign-in link
uv run uvicorn app.main:app --port 8000 &                                         # the server, with its worker
cd .. && npm run dev                                                              # the admin on http://127.0.0.1:5174
```

No AI key is needed to develop or to run the tests; add one in *Platform → AI* when you want agents to think.

## Repository layout

```
backend/app/
  main.py, worker.py      the server (admin API, agent gateway, plugin events) and the worker/scheduler
  admin/                  admin routes (/admin-api/…)
  services/team/          the agent engine: registry, runner, tools, clearance (approvals.py), budgets, events
  services/plugins/       loading plugins, the HTTP connector, signing
  plugin_api.py           the plugin interface (versioned — see docs/PLUGINS.md)
  ai/                     the AI providers (Gemini, Claude, OpenAI)
  admin_docs/             Admin → Docs, one Markdown file per page
backend/migrations/       Alembic migrations
backend/tests/            pytest; a fresh database from the migrations for every run
admin/src/                the admin app (React 19, TypeScript, Tailwind v4); schema.d.ts is generated
plugins/                  the plugins every install ships (Stripe, Database)
examples/plugins/         a complete example plugin
docs/                     the guide, the connection brief, the plugin reference, the plan and the build log
tools/                    the connection check a project's coding agent runs
scripts/, install.sh      install, backup, restore, update
```

### Where a change usually belongs

| You want to… | Change this |
| --- | --- |
| Add a tool agents can use | `backend/app/services/team/tools.py` (the catalogue) — or, if it is about one business, a plugin |
| Add an action agents can propose | a plugin (`Action`), never the core — the core holds no business |
| Support another AI provider | `backend/app/ai/providers.py` and `backend/app/ai/vendors.py` |
| Connect a kind of project | a plugin in `plugins/` (ready-made) or `examples/plugins/` (an example) |
| Add or change an admin page | `admin/src/…` **and** its page in `backend/app/admin_docs/` (a test checks every page and route is documented) |
| Change a database table | `backend/app/models/…` **plus** a migration in `backend/migrations/versions/` |
| Change a route | the route, then regenerate the admin's types (below) |

## Checks

Run these before pushing; CI runs the same, on SQLite and on PostgreSQL:

```bash
cd backend
uv run ruff check app tests ../tools ../plugins ../examples
uv run mypy app
uv run pytest -q
cd ..
npm run api      # after changing a route: regenerates admin/src/schema.d.ts (commit it)
npm run build    # type-checks and builds the admin
```

CI also scans every pull request for committed secrets ([gitleaks](https://github.com/gitleaks/gitleaks), with
[.gitleaks.toml](.gitleaks.toml)), checks the shell scripts with shellcheck, and builds the Docker image.

### Tests

A change needs a test that fails without it. Tests need no network and no API key: providers, projects and Stripe are
faked with `httpx.MockTransport`. To run the suite on PostgreSQL, point `TEST_DATABASE_URL` at an empty database.

## Pull requests

- Branch from `main`; keep the branch short-lived.
- Write the title as what the change does, in the imperative: *Add a Shopify plugin*, *Fix the budget check for L2
  actions*.
- Say **why** in the description, and how you checked it (for anything visible: a screenshot).
- Link the issue: `Closes #123`.
- **Sign off every commit** (`git commit -s`) — see the licence section below. CI checks it.
- A maintainer reviews within a few days. Squash-merge is used, so the pull request title becomes the commit message.

## Adding a dependency

Dependencies are a long-term cost. Before adding one, check it is actively maintained, has a licence compatible with
the AGPL (MIT, BSD, Apache-2.0, ISC, MPL-2.0, LGPL), and cannot be replaced by a few dozen lines of code. Say why in
the pull request.

## Plugins and the plugin interface

The interface in `backend/app/plugin_api.py` and the HTTP protocol in [docs/PLUGINS.md](docs/PLUGINS.md) are versioned
(`API_VERSION`). A plugin written for 1.x must keep working on every 1.x release: add, never break. A change that
breaks plugins needs a discussion and a new major version.

## Releases

Add a section to [CHANGELOG.md](CHANGELOG.md) under the new version, then tag:

```bash
git tag -a v0.2.0 -m "v0.2.0" && git push origin v0.2.0
```

The release workflow re-runs every check on the tagged commit, publishes the Docker image to the GitHub Container
Registry and creates the GitHub release with that changelog section. A tag whose commit does not pass is not
released.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).

## Licence and sign-off

Control Tower is open source under the [GNU AGPL-3.0](LICENSE); a commercial license is available from Dee Empire
(see [LICENSING.md](LICENSING.md)).

Contributions are accepted under a [Contributor License Agreement](CLA.md). It keeps your copyright with you while
letting the project offer Control Tower under both the AGPL and a commercial license — a dual-licensed project cannot
relicense a contribution it only received under the AGPL. You accept it by:

1. signing off your commits — `git commit -s` adds `Signed-off-by: Your Name <you@example.com>`; and
2. ticking the agreement box in the pull request description on your first pull request.

By participating you also agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
