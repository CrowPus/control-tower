key: platform
title: Platform
order: 80
screens: /platform
api: /admin-api/platform, /admin-api/schedules, /admin-api/ai
summary: Settings for the whole install — the AI your team thinks with, the emergency switch, the AI caps, the banner and the schedules of background work.
---
## What it is for

Settings for the whole install. Changes take effect at once and are logged. Only an owner can change them.

## AI

Which AI the agents, Nia's chat, meetings and the brain think with — **Google Gemini**, **Anthropic Claude** or **OpenAI** — with your own key from the provider. Only an owner can change it.

- **Provider** — the one that thinks. You can switch at any time; the next call uses the new one.
- **Keys** — one per provider, stored encrypted and never shown again. Type a new one to replace it; **Remove** takes the admin's key away (a key in the server's .env file then applies again). Where each key comes from is shown: *set here*, *from the server's settings*, or *none*.
- **Models** — a *reasoning* model for analyses and writing, a *fast* one for routine work. Gemini and Claude have defaults; for OpenAI name the models you use.
- **Prices** — what each model costs, in USD per million tokens (input and output). Budgets count with these, so set them for every model you use, from the provider's price list. Gemini's are set already. A Claude or OpenAI model without a price cannot be saved.
- **OpenAI-compatible address** — leave empty for OpenAI; or the address of another service that speaks OpenAI's API (for example OpenRouter, or a model server on your own machine).
- **Test** — one short answer from the chosen provider's fast model: shows that the key, the model name and the address work, and how long it took.

**Gemini only**, whichever provider thinks: pictures and videos (Marketing → Studio), Nia's voice, Nia's browser and web search for agents. They need a Gemini key; without one they say so and the rest works.

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
