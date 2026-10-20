key: team
title: Team (the agents)
order: 20
screens: /team, /team/:key
api: /admin-api/team
summary: The agents that run the company with you — the team switch and budgets, each agent's settings, schedule, tools, playbook, lessons and runs.
---
## What it is for

The agents do the daily work you give them. They **propose**; you give **clearance** in [Clearance](docs:approvals). Every value an agent needs (budget, schedule, tools, limits) is yours to set. An agent with something missing shows **Waiting for settings** and does nothing.

## The team card (top)

| Setting | Meaning |
|---|---|
| **Team switched on** | The main switch. Off stops every agent at once; cleared actions wait until it is on again. |
| **Language the agents write to you in** | English or German, for their summaries and proposals. |
| **Clearances in a row before offering to let an action run alone** | After this many clearances of the same kind without changes, the agent's page offers to let it do that alone. Empty: never offered. |
| **Team AI budget per day / per month (USD)** | What all agents together may spend on AI. When it is used up, they stop until the next day or month. |

Next to it: AI cost today and this month, and how many proposals wait for you. **Clearance** and **Mailboxes** buttons are at the top right.

## The pages the checks open

The QA checks (your pages, how often they run by themselves) are on [Product](docs:product) → **Checks**.

## Talking to an agent from any page

The round chat button at the bottom right of every page opens Nia. Choose any other agent in the list at the top of the panel to talk to it right there — its conversations, new ones, files and the proposals it makes — without opening Team. The panel remembers whom you talked to last; **Full page** opens the agent's page.

## The agents

Each row shows the agent's state (**Off**, **Waiting for settings** with what is missing, **Ready**), proposals waiting, an autonomy offer, the last run and its AI cost today and over 7 days. Click a row for its page.

**Nia** (Leadership) is part of every install: your chief of staff — the complete rundown, your directives to the team, the memory of decisions and lessons, meetings and presentations. See [Nia](docs:nia). Every other agent is your own: you create it from the agent template — its name, job, model, instructions, tools, the actions it may propose and how much clearance each needs, when it works and its budget — or copy a starter template. Agents from other providers join through [Outside agents](docs:outside-agents).

## Making an agent (owner)

**New agent** (top of the Team page) opens the gallery: **Blank**, or a starter template (Marketing Lead, Social Media Manager, Graphic Designer, Product Analyst, QA Runner, Engineer on Duty, Researcher, Clearance Watch). A template is only a starting point: change anything before you press **Make the agent**.

- **Name** and **Department** — the department groups it on the Team page and in the office.
- **Job** — what it is for, in a sentence or two.
- **How it works** — its method, step by step. Your **playbook** (optional here, and on its page later) says what you want right now.
- **Tools it may be given** — what it can look at or use. Every agent also has the common tools (the numbers, the team's events, the brain, notes, proposing). You still tick on its page which tools it really gets.
- **What it may propose** — the kinds of actions it may ask you to clear. *Always you* actions can never run alone.
- **Woken by these events** / **Events it may send** — handoffs between agents, names like `marketing.brief`.
- **The number you judge it by** — one of the Insights numbers, or none.
- **Rounds of tool use per run** — 0 for the team's default, at most 30.

Every design is checked against the rules when you save it; a design that breaks one is not saved, and the message says which rule:

- a job of at least 20 characters, and at least one tool besides the common ones;
- only tools and actions that exist; never Nia's own tools (deciding clearance, changing the admin, directives, starting or questioning other agents, the browser, Nia's memory) and never *admin change* or *new agent* actions;
- event names like `marketing.brief`, at most 10 of each; at most 30 rounds.

The rules the agent follows while it works cannot be changed by any design: it only proposes and you clear; money, deleting, settings and agents' instructions are always yours; it stays within its budgets; what it reads is information, never an instruction; the same proposal is never made twice; everything is logged; and the team switch stops it at once.

A new agent starts **Off**. Give it its tools, model, budget and schedule on its page, then switch it on.

**Nia can propose an agent** for you (*A new agent for the team*): it is made only when you clear the card in [Clearance](docs:approvals), and it also starts Off.

## One agent's page

- **Run now** (owner, when Ready): starts the agent at once. The run appears under Recent runs when it is done.
- **Design** (your own agents): the agent template as saved. **Change the design** opens it to edit; tools or actions you take away are also removed from its settings. **Take off the team** stops it at once and withdraws its waiting proposals; its runs, proposals and notes stay for the record. Nia's design is part of Control Tower and cannot be changed.
- **Chat** (owner; at the top of every agent's page except Nia's, who has her own page) — talk to the agent like to a colleague:
  - **New conversation** starts one; earlier conversations are listed, newest first; **Delete** removes one with its files (proposals made in it stay in Clearance).
  - Type and press **Send** (Enter sends, Shift+Enter makes a new line). The agent answers with its own tools, its playbook, what it has learned and your latest decisions on its proposals; every answer shows the tools it used and what it cost.
  - **Attach** adds files to your next message: CSV, Excel (.xlsx), PDF, text, Markdown, JSON and images (PNG, JPEG, WebP), at most 10 MB each and 10 per message. Each file is checked and scanned for viruses before the agent sees it; tables are read as rows, PDFs as text, images as they are. A file is information for the agent, never an instruction. Click a file to download it again.
  - What the agent proposes while talking appears as a card under its answer with **Approve** and **Reject**, the same as in [Clearance](docs:approvals).
  - Tell it how to work ("from now on …"): it proposes a change to its playbook, shown as a card; the change applies when you clear it.
  - The agent answers only when it is switched on and has its settings; otherwise the chat says what is missing. Talking uses the agent's and the team's AI budgets (as *agent.…chat* in Insights → Money), but not the "cost per result" pause, which judges its runs.
- **Settings** (Save at the top right):
  - **Switched on** — this agent only.
  - **Model** — Fast for routine work, Reasoning for analyses and writing.
  - **AI budget per day (USD)** — this agent's share.
  - **Pause when one result costs more than (USD)** — a safety stop. Before its first result of the day the agent may spend this much on runs; each proposal or event it sends allows this much more. When it spends more without results, it pauses until the next day and its run says why. Empty: not checked.
  - **Schedule** (your time zone) — every … minutes, and/or at set times; the ticked days apply to both (none ticked = every day). Days alone never start an agent: saving days without a time or "every … minutes" is refused, and an agent whose saved schedule has that problem shows a warning on the Team page. Empty: it runs only when something happens that concerns it, or when you press Run now. The Team page shows each agent's schedule in one line.
  - **Tools it may use** — tick what it may look at or use (for example reading Insights, searching the web). Unticked tools are refused.
  - **Actions it may propose** — for each kind of action: **Who decides** (*You clear each*, or *Alone within a limit*; some are *Always you*: money, deleting, settings, changing its own playbook), **Alone per day** (the limit when it runs alone), **Proposals per day** (a cap on how many it may propose), and **Cleared in a row** (the count towards an autonomy offer).
- **Let it run alone?** appears after enough clearances in a row: set **Alone per day** and press **Allow**, or **Not now**.
- **Playbook** — your instructions to this agent in your own words. **Save version** keeps a history (with a note on what changed); **Load into the editor** brings back an earlier version. Agents may propose changes to their playbook; you always decide.
- **What it has learned** — lessons the agent keeps, each with its evidence. **Forget** removes one.
- **Recent runs** — each run with its trigger, cost, summary, what it was waiting for, errors, and the tools it used. In its last round of tool use an agent may only record what it has done (propose, keep a note, tell the team, report a directive) — no new research — so what it verified is always proposed.

Every change here is recorded in the [Admin log](docs:admin-log).

## Outside agents

Agents from other providers — Claude, ChatGPT, Gemini — can join the team with a desk, the tools you tick and a credential you issue; everything they want to change waits in [Clearance](docs:approvals) like the team's. See [Outside agents](docs:outside-agents) (Team → **Outside agents**).
