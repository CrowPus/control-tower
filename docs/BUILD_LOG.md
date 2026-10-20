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
