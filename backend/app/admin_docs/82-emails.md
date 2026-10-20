key: emails
title: E-mails
order: 82
screens: /email-deliveries
api: /admin-api/email-deliveries
summary: Every e-mail the service sent in the last 30 days — delivered, waiting for a retry, or failed and why.
---
## What it is for

Every e-mail the install sends (admin invitations, alerts, the weekly report) is recorded. It is retried while the mail server is busy, and marked when it comes back. Shows the last 30 days.

## Views

- **Failed or returned** — e-mails that did not arrive, with the reason: **Address does not exist**, **Refused by the recipient's server**, **Blocked by the recipient's server**.
- **Waiting for a retry** — the mail server was busy; it is tried again automatically.
- All e-mails.

"Nothing here" means every e-mail in that view went out. A burst of returned e-mails raises an [alert](docs:insights).
