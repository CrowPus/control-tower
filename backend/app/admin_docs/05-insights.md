key: insights
title: Insights
order: 5
screens: /insights, /insights/agents, /insights/money, /insights/performance, /insights/errors, /insights/operations, /insights/alerts
api: /admin-api/insights
summary: The numbers — today, the agents, money, performance, errors, operations — and the alerts that warn you.
---
## What it is for

Whether your agents and the install are working, and what needs you. The numbers come from the install's own database and counters; the worker counts every 15 minutes. Choose the period at the top of each tab. Every chart has a **table view** and most can be **downloaded as CSV** (the download icon on the chart).

## Tabs

| Tab | What it shows |
|---|---|
| **Today** | The few numbers that tell you whether the agents are working — proposals cleared, runs, proposals, rejections, AI cost, server errors — and what needs you now. |
| **Agents** | Runs per agent and how they ended, each agent's AI cost, and your clearance decisions over time. |
| **Money** | The AI cost by feature, including how often each AI feature fails or waits long. AI cost is the provider's estimate in US dollars. |
| **Performance** | Speed and errors of the admin and the agent gateway: response times (95th percentile), server errors, requests per part, uptime checks every minute, and page speed measured in your browser. Fix the slow addresses that are also busy first. |
| **Errors** | Browser errors, grouped (the same error many times is one group; messages are scrubbed of e-mail addresses, links, tokens and numbers). Mark a group **Fixed** (it opens again by itself if the error comes back), **Ignore** it, or **Reopen** it. |
| **Operations** | Requests, background tasks and e-mails per day, by outcome. |
| **Alerts** | What went wrong in the last 30 days, and when to warn you. |

Your project's own numbers (sales, sign-ups, orders) join these tabs when a plugin reports them.

## Alerts

The worker checks every 5 minutes. A new alert is **e-mailed to the alert address** (critical ones start with URGENT) and appears in the bell. It closes by itself once the problem is gone, and you get one more e-mail then. **Close** closes one by hand; it opens again if the problem is still there at the next check.

**When to warn you** (owner only; **Save**, the next check uses the new values):

| Rule | Meaning |
|---|---|
| Server errors: share of requests above | Warn when this share of requests fails. |
| Slow: p95 over the last 10 minutes above | Warn when 1 in 20 requests is slower than this. |
| Speed and error rules stay quiet below this many requests | Avoids alarms when there is little traffic. |
| Failed checks in a row before an address counts as down | Uptime. |
| Background task waiting longer than | A stuck task queue. |
| New error type seen this often within an hour | A new kind of error spreading. |
| Bounce rule stays quiet below this many e-mails | E-mail problems with little volume. |
| AI spend today above this share of the daily cap | The install's AI money running out. |
