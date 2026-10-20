# Build log

## CT0 — repo, licence, plan (2026-10-20)

README, docs/PLAN.md, AGPL-3.0 licence with LICENSING.md, CLA.md and NOTICE. The owner decided D1–D6 (PLAN §11).

## CT1 — the core taken from Epliv (2026-10-20)

The owner copied Epliv's backend, admin app and build files into this repo (no Epliv history) and agreed, for Dee
Empire, to release them under AGPL-3.0. Then everything that belongs to Epliv was removed:

- **Removed**: the member API and member app, the website, opportunities, discovery and research, travel rules,
  partners, ads, outcomes, showcase, members, questionnaire, plans and payments (Stripe), support chat, customer success,
  push notifications, member accounts and their isolation layer (`core/tenancy.py`, database roles), 91 Epliv
  migrations, Epliv's agents (Scout, Verifier, Travel Rules Keeper …) and their action types, Epliv's admin pages and
  docs, Epliv's logo and design tokens.
- **One server** (`app.main`): the admin, the agent gateway (MCP + REST + OAuth) and the studio's media links. The
  admin's IP allowlist applies to everything except the gateway, OAuth discovery and media links.
- **One database connection**, no tenancy; **one migration** (`0001_core`) generated from the models on PostgreSQL.
- **The owner's time zone** (`TIME_ZONE`, `core/clock.py`) replaces Europe/Berlin everywhere.
- **The company** in prompts: the brand kit gained `about` (what the company does; every agent reads it) and
  `pronunciation`; `name` defaults to `APP_NAME`. Studio, video and agent prompts use them instead of Epliv's text.
- **Agents**: the registry ships only Nia. Departments come from the agents. Build-stage gating (`BUILT_STAGES`) is
  gone. Outside agents: no cap of ten (the owner may set one), credentials `ct_agent_…` / `ct_oat_…`.
- **Clearance**: Admin → Approvals is now Admin → Clearance (`/clearance`; `/approvals` redirects). The API path stays
  `/admin-api/team/approvals` for now.
- **Insights**: Epliv's member KPIs replaced by the agents' work (runs, proposals, cleared, rejected, cost per agent),
  AI cost, requests and errors; new Agents tab; plugins' numbers (`plugin.*` metrics) survive the snapshot.
- **QA checks**: the owner's own list of pages (name, address, text it must contain) instead of Epliv's member journey.
- **Trust**: deletion runs and proposals waiting for clearance longer than the owner's rule.
- **Office**: open desks for any number of the owner's agents, looks generated per agent, department colours from
  names; local time instead of Berlin.
- **Admin app**: Control Tower name, an SVG tower mark, its own theme (cyan primary), English wording throughout.
- **Mail**: only admin mails (invitation, attention, weekly report); English by default.

Checks: 202 backend tests pass on SQLite and on PostgreSQL (`scripts/check-container/postgres.sh`); ruff and mypy
clean; the admin type-checks and builds. A fresh install (empty database → migrations → `admin_cli create-owner` →
sign-in with password and authenticator) shows Nia alone, nothing in Clearance, and the gateway refuses calls without a
credential; screenshots of Team, Clearance, Insights, Marketing → Brand, Product → Checks, Overview, Platform and Trust
rendered without page errors (light, dark, phone).

Not yet done (later stages): the agent template and starter templates (CT2), the plugin interface (CT3), the
installer and first-run wizard (CT6). The Docker image and compose file are rewritten but not yet built and run as a
whole.

Nia's avatar videos (`admin/public/assets/nia/`): the owner confirmed on 2026-10-20 that they may be published with Control Tower.

## CT2 — the agent template (2026-10-20)

The owner builds agents in the admin; the core only sets the rules.

- **`owner_agents` table** (migration `0002`): one row per agent of the owner's — the agent template's fields (name,
  department, job, how it works, tools, actions, events, goal number, rounds), the starter template it began from,
  active or archived.
- **The team is read live** (`registry.AGENTS`): built-in agents (only Nia) plus the owner's active agents from the
  database, re-read every few seconds in each process, at once after a change in the same process. Everything that
  reads the team (dispatcher, runner, Team page, office, brain) sees new agents without a restart.
- **Rules on save** (`services/team/agents.py`): a design is refused, with the rule it breaks, when its job is too
  short; it has no tool beyond the common ones; it names a tool or action that does not exist; it asks for Nia's own
  tools or for `admin.change` / `agent.create`; an event name is malformed or there are too many; its goal is not an
  Insights number; or it wants more than 30 rounds. Built-in agents cannot be redesigned or archived. Keys come from
  the name, unique, never a built-in key. The rules while working are the engine's as before (§5) and apply to every
  owner agent unchanged.
- **Make, change, take off**: a new agent starts switched off with its design's tools pre-granted; a changed design drops
  settings it no longer allows (tools, levels, limits); taking it off switches it off, withdraws its waiting proposals and
  keeps its history.
- **Starter templates** (`services/team/templates.py`): Marketing Lead, Social Media Manager, Graphic Designer, Product
  Analyst, QA Runner, Engineer on Duty, Researcher, Clearance Watch — written for any company (Epliv's agents, made
  generic). Each passes the rules (tested).
- **Nia proposes agents**: new action `agent.create` (always the owner, L3) and read tool `agent_templates`. The
  proposal is checked against the same rules when Nia makes it; one waiting proposal per name; the agent is made only
  after clearance, switched off.
- **Admin API**: `GET /admin-api/team/catalogue`, `POST /admin-api/team/agents`, `PUT /admin-api/team/agents/{key}/design`,
  `DELETE /admin-api/team/agents/{key}`; every change in the admin log.
- **Admin app**: Team → **New agent** (template gallery, then the design form), and a **Design** card on each of the
  owner's agents (change, take off the team). Docs: Team → "Making an agent".
- Also removed leftover Epliv words in the team's tool and action descriptions and in the proposal target list.

Checks: 219 backend tests pass on SQLite and on PostgreSQL (17 new in `test_9_owner_agents.py`: every template keeps
the rules, each rule refuses, a template agent runs and is refused Nia's tools and foreign actions, unique keys,
redesign and archive, Nia's proposal made only after clearance, the admin API); ruff and mypy clean; migration
upgrade/downgrade and `alembic check` on PostgreSQL; the admin builds. In a browser on a fresh install: New agent →
QA Runner → Make the agent → its page with the design open; a too-short job is refused with the rule; no page errors,
light, dark and phone width.

## CT3 — the plugin interface (2026-10-20)

Projects connect to the core; the core still knows no business. The owner's first project will be a fresh clone of
Zelyq (CT4), so the interface is ready for a project in any language: Python plugins and an HTTP connector, one contract.

- **The interface** (`app/plugin_api.py`, version 1.0): `Plugin` with tools (Records), actions, metrics (Signals),
  starter templates, settings and the fields that are personal; a `Context` for the plugin's code (its settings, the
  agent, its own data folder, `emit`). Developer guide: `docs/PLUGINS.md`.
- **Python plugins**: `plugins/<key>/plugin.py`, imported as their own package; a broken one is reported and left out
  (`services/plugins/loader.py`). Docker: `plugins/` is in the image and mounted from the host in compose.
- **HTTP connector** (`services/plugins/connector.py`): the project serves `/manifest`, `/tools/…`,
  `/actions/…/check|recheck|execute` and `/metrics`; the manifest is validated and becomes the same `Plugin`.
  Every request is signed both ways (HMAC-SHA256 over time, path and body; five minutes; `services/plugins/signing.py`).
- **Registration** (`services/plugins/core.py`): tools `<plugin>_<tool>`, actions `<plugin>.<action>` with executor,
  type checks, `check`/`recheck` and duplicate target, Insights numbers `<plugin>_<metric>`, templates
  `<plugin>.<template>`. Taken names refuse the plugin. Each plugin starts **off**: hidden from the agent template,
  templates and catalogue, and every call refused; a cleared action carried out after it was switched off fails with
  that reason. On/off, settings (secrets encrypted) and the shared secret are platform settings, re-read every few
  seconds in each process.
- **Personal fields** the plugin names are hidden at any depth in tool answers, action results, views and events.
- **Events in**: `POST /plugin-api/v1/<plugin>/events`, signed, outside the IP allowlist, 50 events / 64 KB a request.
- **Numbers**: `plugins.collect` every 15 minutes writes today's values (`plugin.<key>.<metric>`, per dimension);
  Insights → Today shows the switched-on plugins' numbers.
- **Admin → Plugins**: list, problems, on/off, settings, new shared secret (shown once), connect / fetch / remove an
  HTTP project, views (a tool shown as a table, personal fields hidden), collect now. Docs: Admin → Docs → Plugins.
- **Example**: `examples/plugins/hello_shop` (two tools, an L2 action with check/recheck/target, two numbers, a
  starter agent, a required setting, a personal field) — the tests load it.

Checks: tests in `test_9_plugins.py` — the example loads off and is offered only when switched on; an agent from its
template reads (e-mails hidden), proposals are checked (sold out, duplicate, missing field), the cleared action runs
once and emits its event; switched off, a cleared action fails with the reason; numbers reach Insights and are
replaced, not added; broken folders are reported (crash, no plugin, wrong key, bad name, future API, taken names, bad
target); signatures bind time, path and body; a fake HTTP project is connected, fetched, switched on, used by an agent
(tool, check, L3 action, metric, view), sends signed events (unsigned, wrong, stale and old-secret refused) and is
removed; the owner cannot switch a plugin on without its required settings. Full suite on SQLite and PostgreSQL.
