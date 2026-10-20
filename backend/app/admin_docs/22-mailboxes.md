key: mailboxes
title: Mailboxes
order: 22
screens: /team/mailboxes
api: /admin-api/team/mailboxes
summary: The team's own mailboxes (the marketing mailbox) — address, login, servers, encrypted password and a connection test.
---
## What it is for

The mailboxes the agents use, separate from the install's own mailbox that sends sign-in e-mails. Today there is the **Marketing mailbox**: the address the company shows in public. No agent sends from it on its own; you read and answer it yourself in [Mail](docs:mail).

## Fields

All values come from your mail provider; none is set in the code.

| Field | Meaning |
|---|---|
| Address (sender) | The e-mail address answers are sent from. |
| Sender name | The name recipients see. |
| Login | The user name for the mail servers (often the address). |
| Password | Stored encrypted and never shown again. Type a new one to replace it; **Remove password** deletes it. |
| SMTP server, port, security | For sending (SSL/TLS or STARTTLS). |
| IMAP server, port | For reading (SSL). |

The badge says **Complete** or lists what is **missing**.

## Buttons

- **Save** — keeps the values (owner only).
- **Test connection** — signs in to both servers without sending or reading anything, and shows whether sending (SMTP) and reading (IMAP) worked.

## In Mail

The marketing mailbox can be added to [Mail](docs:mail) with one click (Mail → ⚙ → Accounts → **Add marketing mailbox**). Mail then reads and sends with the settings on this page, so a new password is entered here only once.
