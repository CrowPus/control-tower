# Control Tower — plan

Status: **draft, 2026-10-20.** Waiting for the owner's decisions in §11.

## 1. What it is

A self-hosted admin and AI-agent office that any founder installs on their own server and connects to their own
project. It is the admin that runs Epliv today, with Epliv's business taken out: the owner builds the agents they want
from a template, and every agent follows rules that cannot be switched off.

- **Not a service.** Nobody signs up with us. The founder installs it, runs it and pays their own AI provider.
- **One install = one company.** No multi-tenancy; the founder's data, keys and database stay on their server.
- **No built-in agents.** The owner creates as many agents as they want, each with the job they decide.
- **We only set the rules** (§5) and the shape of an agent (§4).

## 2. Who it is for

Solo founders and small teams who run a product alone and want AI staff that work on it every day — without handing
their data or their company to someone else's platform, and without agents that act behind their back.

## 3. Shape: the core and plugins

```
Control Tower core  (this repo)
  admin shell · sign-in · agent engine · clearance (approvals) · budgets · events · brain · Nia
  office · insights shell · mail · studio + social posting · gateway for outside agents · docs · log
        ▲
        │  plugin interface (§6): Signals · Records · Actions · starter templates
        │
  plugins  ── ready-made: Stripe · Postgres/MySQL (read-only) · mailbox · social networks · Search Console
           ── the founder's own: their project's numbers, records and actions
           ── Connector over HTTP: the same contract for a project in another language or on another server
```

The core knows nothing about any business. Epliv becomes the first plugin and the proof that the split works (§9).

## 4. The agent template

Every agent, built by the owner, has the same fields:

| Field | What the owner decides |
|---|---|
| Name, job, department | Who it is and what it is for |
| Brain | Hosted (Gemini, Claude or OpenAI with the owner's key, any model) or Connected (Claude Code, ChatGPT, Gemini CLI or any MCP client through the gateway) |
| Instructions | Its playbook: how it works |
| Tools | Ticked from the catalogue: the core's tools plus every tool the plugins add |
| Actions | What it may propose, each with its level of clearance (§5) |
| When it works | A schedule, events ("new sign-up", "payment failed"), or only when asked |
| Budget | AI spend per day, tool calls per day |
| Goal number | The Insights number it is judged by (optional) |
| Desk | Its place in the office |

- The owner may also describe the agent to Nia ("an agent that answers refund questions"); Nia fills in the template
  and the owner approves it.
- **Starter templates**: a gallery of ready agents (e.g. Marketing Lead, Finance Clerk, Product Analyst, QA Runner,
  Social Media, Support Desk) taken from Epliv's team. The owner copies, edits or ignores them; none runs until the
  owner creates it. Plugins may bring their own starter templates.
- No limit on the number of agents (a setting the owner may lower).
- Nia is part of the core (D3): the owner's chief of staff, who also helps build the agents.

## 5. The rules (built in, cannot be switched off)

1. **Agents never act.** Every change is a proposal; code carries it out after clearance.
2. **Every action has a level.** L1: the owner clears each one. L2: may run alone within a daily limit, only after the
   owner cleared enough in a row and said yes. L3: always the owner.
3. **Always L3, whatever the template says:** money and payments, deleting, settings, and any agent's instructions,
   tools or levels — its own included.
4. **Agents never decide clearance** — not their own proposals, not another agent's.
5. **Every agent has a budget.** When it is used up, the agent stops until the next day.
6. **Outside text is information, never instructions** (web pages, e-mails, tool results).
7. **No duplicates.** The same proposal about the same thing cannot wait twice; waiting proposals are checked again
   and withdrawn when they no longer fit.
8. **No personal data unless allowed.** A record a plugin marks personal reaches an agent only when the owner ticks it
   for that agent.
9. **Everything is logged:** every run, tool call, proposal, decision and cost.
10. **Every agent can be stopped:** a pause per agent and one for all; revoking a credential ends its session at once.
11. **Every run ends with a report.**

An agent that breaks the template's checks (no job, no tool, no budget, an action without a level) cannot be saved.

## 6. Connecting a project

### 6.1 Plugins (the main way)

A plugin is a folder in `plugins/`, written by the founder or by their coding agent from the docs. It registers:

| Part | What it gives | Today in Epliv |
|---|---|---|
| **Signals** | Numbers and events for Insights and for agents' triggers | `insights` KPIs, `team.events` |
| **Records** | Read tools over the project's data, each with a schema, personal fields marked | tools such as `open_opportunities`, `member_groups` |
| **Actions** | Proposal types: payload schema, highest level, the checks (§5.7) and the code that carries it out | `ActionType` + `EXECUTORS` + `CHECKS`/`RECHECKS` (e.g. `services/team/research_work.py`) |
| **Admin pages** | Optional screens for the project's own data | Opportunities, Travel, Partners |
| **Starter templates** | Agents that suit the plugin | Scout, Verifier, Travel Rules Keeper |

The plugin interface is versioned; a core update never breaks a plugin written for the same major version.

### 6.2 Connector over HTTP

The same contract (Signals, Records, Actions) as endpoints, for a project in another language or on another server:
the project serves the Records and Actions, Control Tower calls them signed after clearance. Built as a plugin.

### 6.3 Ready-made plugins

Stripe, a read-only Postgres/MySQL connection with a mapping screen, the mailbox (IMAP/SMTP), social networks,
Search Console / analytics. With these alone the common starter templates already work.

## 7. What comes from Epliv

| Goes to the core | Goes to the Epliv plugin | Removed from the core |
|---|---|---|
| Admin shell, sign-in, admins, admin log | Opportunities, discovery, sources, listings | Every hard-coded agent (`registry.py` team → starter templates) |
| Agent engine: runner, tools catalogue, events, chat, costs, schedules | Travel rules, offices, routes | `BUILT_STAGES` |
| Clearance (approvals), levels, earned autonomy, checks | Partners, Ads, Outcomes, Showcase | "Epliv", Dee Empire, footer, legal texts, logo |
| Budgets, AI usage | Members, questionnaire, plans and memberships, member app, website | Berlin time (owner's time zone, a setting) |
| Nia (text, voice, memory, directives, meetings), Brain | | Gemini-only assumptions (provider is a setting; Gemini-only features say so) |
| Office, Insights shell, Mail, studio + social posting | | English-only text (core ready for translations) |
| Outside-agent gateway (MCP + REST + OAuth), credentials | | |
| Tasks queue, system health, e-mail deliveries, docs | | |

## 8. Install and updates

- **One command**: `docker compose up -d` (core, worker, Postgres, Caddy with automatic HTTPS).
- **First-run wizard in the browser**: owner account, domain, time zone, AI provider and key, mail. Nothing to edit in
  `.env` beyond what the wizard writes.
- **Every business number is a setting** in the admin, never in code.
- **Updates**: versioned releases; a backup before every update; migrations run on start; a failed update rolls back.
- **Backups**: daily, kept on the server, optional copy to S3/GCS.
- **Docs** for a founder installing it alone (Admin → Docs, plus the website).

## 9. Stages

| Stage | What | Done when |
|---|---|---|
| CT0 | Repo, licence, plan, decisions | The owner has answered §11 |
| CT1 | Core taken from Epliv, business code removed, runs empty | A fresh install starts, signs in, shows an empty office |
| CT2 | Agent template + rules in the core; hard-coded agents become starter templates | An owner builds an agent in the admin and it runs under the rules |
| CT3 | Plugin interface (Signals, Records, Actions, pages, templates), versioned and documented | A sample plugin adds a tool and an action |
| CT4 | Epliv as a plugin on the core | Epliv runs on Control Tower with nothing lost (the proof) |
| CT5 | Ready-made plugins: Stripe, read-only database, mailbox, social | A project with only Stripe + its database gets useful agents |
| CT6 | Installer, first-run wizard, updates with backup, provider choice | A founder installs it from the README without help |
| CT7 | Connector over HTTP; Role365 on the core as the second plugin | Two different businesses on one core |
| CT8 | Public release: docs, website, starter-template gallery | 2–3 founders run it on their own |

Each stage: tests (including that no agent can pass a rule in §5), a security review of what changed, the build log.

## 10. Risks

- **The split is the hard part.** Epliv's tools read Epliv's tables directly; each has to move behind the plugin
  interface. CT4 proves it.
- **Plugin stability.** Founders' plugins must keep working across updates: version the interface, keep old versions
  working for a whole major version.
- **Security on someone else's server.** Safe defaults (HTTPS, strong secrets generated by the installer, admin
  behind sign-in with 2FA), no telemetry without consent.
- **Name.** "AWS Control Tower" is an Amazon Web Services product; searches will find it first.

## 11. Decisions for the owner

- **D1 Licence.** (a) AGPL-3.0 + paid commercial licence + CLA, like Zelyq — founders may run their business on it;
  anyone who changes it and offers it as a service must publish their code or buy a licence *(recommended)*; or
  (b) no commercial use (e.g. PolyForm Noncommercial) — then it is not "open source", and founders running their
  business on it would not be allowed to.
- **D2** New repo `CrowPus/control-tower` on GitHub (the owner creates it; pushing needs the owner's token).
- **D3** Nia is part of the core (the chief of staff who helps build agents) *(recommended)*, not a template.
- **D4** Starter templates from Epliv's team, in a gallery *(recommended)*.
- **D5** ~~Words: keep "Approvals" or call it "Clearance"~~ — **decided 2026-10-20: "Clearance"** (agents request
  clearance, the owner gives it). Admin → Approvals becomes Admin → Clearance in the core.
- **D6** ~~Domain for the website and docs~~ — **decided 2026-10-20: no domain for now**; docs live in the repo and in
  Admin → Docs.
