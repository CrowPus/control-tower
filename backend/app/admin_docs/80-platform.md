key: platform
title: Platform
order: 80
screens: /platform
api: /admin-api/platform, /admin-api/schedules
summary: Settings for the whole install — the emergency switch, the AI caps, the banner and the schedules of background work.
---
## What it is for

Settings for the whole install. Changes take effect at once and are logged. Only an owner can change them.

## Emergency switches

For everything at once:

| Switch | What it does |
|---|---|
| **Pause all automation** | No agent runs and no background work is done until it is off. |

When one is on, [Live overview](docs:overview) shows a warning.

## The install

The public address the gateway and media links use (`PUBLIC_BASE_URL`), and the install's AI caps a day and a month (`AI_DAILY_BUDGET_USD`, `AI_MONTHLY_BUDGET_USD`): the last safety rail for all AI use. Each agent also has its own budget in [Team](docs:team). **Banner on every admin page**: a short line every admin sees (empty: none).

## Schedules

How often each background job runs (5 minutes to 24 hours), and on/off per job.

If the install's mailbox (`SMTP_*` on the server) is not configured, a warning says that admin invitations and alerts cannot be e-mailed.
