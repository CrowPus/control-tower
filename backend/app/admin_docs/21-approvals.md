key: approvals
title: Clearance
order: 21
screens: /clearance, /approvals
api: /admin-api/team/approvals
summary: What the agents propose — clear it, edit then clear it, or reject it with a reason; several at once.
---
## What it is for

Every action an agent wants to take that reaches outside the admin, costs money or changes what customers see waits here for your clearance. **Nothing happens until you clear it.** Cleared actions are carried out within about a minute (if the team is switched on).

## Tabs

**Pending** (what waits for you), and the decided ones by state. The Team page and the bell show how many are pending.

## One proposal

Each card shows the agent, what it wants to do, **Why** (its reason and evidence), **Expected effect**, and **Details** (exactly what would be done). The badge says who decides:

| Badge | Meaning |
|---|---|
| You clear it | You decide each one. |
| Alone within your limit | You let this kind run alone up to a daily number (set on the agent's page); shown for your information. |
| Always you | Never runs alone: money, deleting, settings, any agent's playbook. |

An agent never has two open proposals about the same thing: while one waits (for example a post for one network, or an idea with the same title), a second one is refused and the agent is told which card is waiting. Once you decide it, the agent may propose again. A playbook change is different: it always holds the whole new playbook, so a newer one replaces the one still waiting (shown as *withdrawn*).

## Buttons

- **Approve** — carry it out as proposed.
- **Edit, then approve** — change the details first (they are shown as editable text), add an optional note, then **Approve with changes**. An edited clearance does not count towards "cleared in a row".
- **Reject…** — write why. The agent reads your reason and learns from it.

## Nia's admin changes

A card from Nia titled with a change in the admin shows the exact request (method, address and values). When you press **Approve**, **your own admin page sends it** with your sign-in, then records the result: **Done**, or **Failed** with the admin's answer (for example a value out of range). These cards are decided one at a time and are not part of "Select all". See [Nia](docs:nia).

## Several at once

Tick proposals (or **Select all**), then **Approve N** or **Reject N**.

Every decision is recorded in the [Admin log](docs:admin-log). How much each agent may do alone is set on its page in [Team](docs:team).
