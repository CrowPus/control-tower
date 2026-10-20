key: signing-in
title: Signing in and your account
order: 2
screens: /accept-invite
api: /admin-api/auth
summary: Invitation, password, authenticator app, sessions, lock-outs and signing out.
---
## Your first sign-in

1. An owner invites you on [Admins](/admins). You receive an e-mail with a link (valid for **72 hours**, works once). If the e-mail could not be sent, the owner sees the link and can pass it on.
2. The link opens **Accept invitation**: you choose your password.
3. You sign in with e-mail and password, then **set up an authenticator app** (for example Google Authenticator or 1Password): scan the code it shows and type the six digits. This is required for every admin.

## Every later sign-in

E-mail, password, then the six-digit code from your authenticator app.

## Staying signed in

- A session ends after a fixed number of hours, or earlier after a period without activity. Both durations are server settings (`ADMIN_SESSION_HOURS`, `ADMIN_IDLE_MINUTES`).
- **Sign out** at the bottom of the sidebar ends the session at once.
- The admin may be limited to certain internet addresses (`ADMIN_IP_ALLOWLIST`); from anywhere else it does not open.

## Too many wrong attempts

After repeated wrong passwords or codes, signing in pauses for that account and address for a while (`LOGIN_LOCKOUT_MINUTES`). Wait, then try again; an owner cannot shorten it.

## Lost your phone?

An owner presses **Reset authenticator** for you on [Admins](/admins). At your next sign-in you set up the app again. An owner who loses their own authenticator needs another owner, or the server's command line (`admin_cli`).

## The bell

The bell next to your name lists alerts, open incidents and proposals that could not be carried out. Opening it marks them as seen for you.
