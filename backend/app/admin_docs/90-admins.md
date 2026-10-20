key: admins
title: Admins
order: 90
screens: /admins
api: /admin-api/admins
summary: Owner only — invite admins, choose owner or support, disable them, reset their authenticator.
---
## What it is for

The list of people who can sign in to the admin. Only owners see this page.

## Invite an admin

Fill in **Name**, **E-mail** and **Role** (Support or Owner), then **Invite**. The install e-mails them a link valid for 72 hours. If the e-mail fails, the link appears under the form so you can send it yourself. See [Signing in](docs:signing-in).

## The list

| Column | Meaning |
|---|---|
| Role | Change it with the drop-down (not for yourself). Owner can do everything; Support can work in the admin but not change settings, admins or money. |
| Status | invited, active or disabled. |
| Authenticator | "set up" once they have finished the authenticator step. |
| Last sign-in | When they last signed in. |

## Buttons

- **Disable / Enable** — a disabled admin cannot sign in; their sessions end. Nothing they did is undone.
- **Reset authenticator** — for a lost phone: at their next sign-in they set up the app again.

You cannot change your own role or disable yourself, so the admin always keeps an owner. Every change appears in the [Admin log](docs:admin-log).
