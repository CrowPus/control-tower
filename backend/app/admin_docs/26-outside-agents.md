key: outside-agents
title: Outside agents
order: 26
screens: /team/outside, /team/outside/:id
api: /admin-api/outside
summary: Agents from other providers — Claude, ChatGPT, Gemini … — working in the company like the team: a desk, the tools you tick, a credential you issue, and every change waits for your clearance.
---
## What it is for

Agents from outside can work in the company, like the team: Claude (Claude Code or Claude.ai), ChatGPT, Gemini or any agent that speaks MCP. Each gets a **desk** in the office (the outside agents' wing), the **tools you tick**, the **actions it may propose** — each waits for you in [Clearance](docs:approvals) — and, before it can start, a **credential** you issue. It never sees the install's code, servers or database: only its tools. Open it from [Team](docs:team) → **Outside agents**.

## Two kinds

- **Connected** — it brings its own model and connects to the install's **Agent Gateway** (`<your address>/agent-gateway/mcp`) with its credential. Whoever runs it pays for its thinking. It works when its operator starts it.
- **Hosted** — the install runs it on its own server with **your API key** for its provider (Anthropic for Claude, OpenAI for GPT, Google for Gemini), on the times you set or when you press **Run now**. The key is stored encrypted and never shown again.

## Hiring one

1. **Hire an outside agent**: its name, its job in a few sentences (it reads this first), department, desk (1–10 in the outside wing; more take open desks), kind; for a hosted one the provider, the model's name (e.g. claude-sonnet-5-5, gpt-5, gemini-3-flash), the provider's API key and when it works.
2. **Tools it may use**: tick from the team's tools. Some are never offered, also not with clearance: admin changes, deciding clearance, settings, other agents, Nia's memory, the browser, pictures that cost money, anyone's personal data.
3. **What it may propose**: the action types it may send to Clearance (e.g. a social post, a product idea). Each one waits for you. It can never change its own playbook.
4. **Limits**: tool calls a day (else the company's number), AI tokens a day for a hosted one, and for a connected one the addresses it may connect from.
5. **Issue credential** (connected ones): shown **once**, with copy-paste set-up for Claude Code, Gemini CLI, the Claude.ai and ChatGPT connectors and plain HTTPS. The install keeps only a fingerprint and the first characters. **Rotate** issues a new one and stops the old one at once.

Its status moves from **Needs its credential** to **Waiting to connect** to **Working** at its first call.

## While it works

- It starts with `my_job` (its job, tools, what it may propose, your playbook, the house rules), reads `my_directives` (your confirmed directives for it) and `my_messages` (what you or Nia told it), works with its tools, proposes what should change, and ends with `file_report`.
- **Every call** is on its page: when, which tool, the inputs, ok or refused, from where. Its **reports** are there too, and in the office it sits at its desk — working while it calls, waiting, paused.
- **Message it** from its page; it reads the message the next time it works. Nia can leave it a question for you the same way.
- **Your playbook for it**: standing instructions it reads with its job.

## Stopping it

- **Pause** stops it at once (its calls are refused) until you **Resume**. **Pause every outside agent** in the company rules stops all of them.
- **Revoke** ends its credential and every connector sign-in at once and frees its desk; then you can **Remove** it with its call log (its reports and proposals stay in the team's history).
- A credential **expires** after the company's number of days (90 at the start); issue a new one in time.

## Connecting a connected agent

- **Claude Code**: run the `claude mcp add …` line shown with the credential.
- **Gemini CLI**: put the shown block in its settings.json.
- **Claude.ai or ChatGPT**: add a custom connector with the gateway's address; when it asks to sign in, the install's page asks for the agent's credential — paste it. The connector then gets short-lived access that ends when you revoke or rotate.
- **Anything else**: `GET /agent-gateway/tools` and `POST /agent-gateway/tools/<name>` with `Authorization: Bearer <credential>`.

Nia cannot hire, change, issue credentials for or revoke outside agents: that is only you, on this page.
