key: my-account
title: My account
order: 2
screens: /account
api: /admin-api/auth/password
summary: Your own sign-in details — change your password.
---
## Change your password

**My account** (above *Sign out* in the sidebar) → **Change password**: your **current password**, then the **new password** twice (at least 12 characters; a password manager helps).

- Your **other sign-ins** (another browser or device) end at once; the one you are using stays.
- Your **authenticator app** stays the same.
- The change is in the [admin log](docs:admin-log). A wrong current password is logged too.

## Forgotten password

Ask another owner to reset your authenticator under [Admins](docs:admins), or create a new owner invitation on the server (`python -m app.admin_cli create-owner`).
