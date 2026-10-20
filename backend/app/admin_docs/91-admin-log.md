key: admin-log
title: Admin log
order: 91
screens: /log
api: /admin-api/audit
summary: Every admin sign-in and change, with who, when, from where and the details; nobody can edit it.
---
## What it is for

The record of everything done in the admin: sign-ins (also failed ones), every setting changed, every clearance decision, every action on an agent or a setting. Use it to answer "who changed this, and when?".

## Reading it

| Column | Meaning |
|---|---|
| When | Date and time of the action. |
| Admin | Who did it (empty for the system or a failed sign-in with an unknown e-mail). |
| Action | What was done, e.g. `team.agent_changed`, `trust.rules_changed`. A red badge means it was refused or failed. |
| Target | What it was done to, e.g. `agent nia`. |
| From | The internet address it came from. |
| Details | The values before and after, or the reason given. |

## Filter

Type part of an action in **Filter by action** (for example `team.` or `platform`) to see only those. 50 entries per page, newest first.

Nobody, not even an owner, can change or delete an entry.
