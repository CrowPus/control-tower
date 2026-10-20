key: system
title: System health
order: 81
screens: /system
api: /admin-api/system, /admin-api/health
summary: Whether the parts of the service are running — database, Redis, worker, scheduler, service mailbox, AI key, and the task queues.
---
## What it is for

A quick technical check that every part of the service works. It refreshes every 15 seconds.

## Checks

| Check | Meaning when it is not OK |
|---|---|
| **Database** | Nothing can be read or saved. |
| **Redis** (live events between processes) | Live updates (e.g. Nia's voice, notifications) stop. |
| **Worker** | Background work (e-mails, agents, counts) stops. |
| **Scheduler** | Regular jobs are not started. |
| **Service mailbox** | Sign-in and confirmation e-mails cannot be sent (`SMTP_*` on the server). |
| **AI key** | AI features fail. |

Each shows its **State** and a **Detail**; **Attention** marks what needs looking at.

## Queues

Per queue: **Queued**, **Running**, **Succeeded** and **Failed** tasks. Details are on [Task queue](docs:tasks). Problems also raise [alerts](docs:insights).
