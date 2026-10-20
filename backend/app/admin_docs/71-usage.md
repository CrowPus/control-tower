key: usage
title: AI usage
order: 71
screens: /usage
api: /admin-api/ai-usage
summary: What the AI costs — per day, by model and by feature; estimated from token counts.
---
## What it is for

What the AI costs, by day, by model and by feature. Amounts are estimated from token counts at the configured prices (US dollars).

## What you see

- **Period** — choose how many days.
- **AI spend per day**, **Average per day**, **Model calls**.
- **Spend per day (USD)** — a chart.
- **By model** — calls and cost per AI model.
- **By feature** — calls and cost per kind of AI work (each agent's runs, Nia's voice, pictures, videos).

The agents' own AI cost is shown on [Team](docs:team); costs by feature are under [Insights → Money](docs:insights).

## Budgets

Each agent has its own budget, and the team has one for all agents ([Team](docs:team)); pictures and videos have theirs ([Marketing](docs:social)). Above them all, the install's daily and monthly cap ([Platform](docs:platform)) stops every AI call once it is reached.

## What a cost is based on

Every model is priced at its provider's list price for that model, voice by the minutes heard and spoken, pictures per picture and videos per second (the prices are server settings; change them with the provider's prices). The **Team** page shows everything the agents' AI work cost — each agent's and Nia's own work, the studio's pictures and the videos — with each part under the total.
