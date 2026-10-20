key: tasks
title: Task queue
order: 70
screens: /tasks
api: /admin-api/tasks
summary: Background work — status, retry, cancel. Payloads stay hidden because they can hold your data.
---
## What it is for

The worker does background work: agent runs, sending e-mails, counting statistics, checks, mail sync and more. Each piece of work is a **task**. Payloads and results stay hidden here because they can hold your data.

## The list

Filter by **Status**: queued, running, failed (will be tried again), dead (gave up after its attempts), succeeded, cancelled. Each task shows its kind, queue, when it was created and its attempts.

## Buttons

- **Retry** — run a failed or dead task again.
- **Cancel** — stop a queued task.

How often each scheduled job runs is set under **Schedules** on [Platform](docs:platform). Failed tasks of the last 24 hours are counted on [Live overview](docs:overview); a long-waiting queue raises an [alert](docs:insights).
