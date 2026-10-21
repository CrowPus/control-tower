key: connect-your-project
title: Connect your project
order: 84
screens: /plugins/connect, /plugins/connect/:key
api: /admin-api/plugins/connect-kit, /admin-api/plugins/connections
summary: A guided way to connect your product — app, website, Stripe or database — written for owners who are not developers, with the exact message for your coding agent and a check that says what to fix.
---
## What it is for

Your agents can only see your product once it is connected. **Plugins → Connect your project** walks you through it, step by step, without needing to know how it works inside. Open it again at any time from a connected project's card (**Setup and check**).

## The steps

1. **What is your project?**
   - *My app or website* — any language, anywhere it runs. A small part is added to your product by your coding agent (Claude Code, Cursor, Copilot, Lovable …) or a developer. Recommended.
   - *My payments are on Stripe* — no code: the ready Stripe plugin (see [Plugins](docs:plugins)).
   - *Only numbers from my database* — the ready Database plugin.
   - *A Python plugin on this server* — for developers.
2. **Name it and say where it runs** — a name, a short key (filled in for you) and your product's web address (e.g. `https://shop.example.com`). Control Tower shows the address it will call (your address followed by `/control-tower`). **Create the connection.**
3. **Your project's settings** — three lines to put in your product's settings (its `.env` file, or the environment settings in your hosting dashboard): `CONTROL_TOWER_SECRET`, `CONTROL_TOWER_URL` and `CONTROL_TOWER_KEY`. The secret is shown **only once**: copy it now. Lost it? **New secret** makes another (the old one stops working).
4. **Teach your project to talk to Control Tower**
   - **Download the brief** and save it in your product's main folder as `CONTROL-TOWER.md`. It tells a coding agent everything: the rules (read-only first, no personal data to agents, nothing that changes data unless you ask), how to build it, and how to test it.
   - Optionally say, in your own words, **what your agents should see** ("new sign-ups per day, the newest customers").
   - **Copy the message** and paste it into your coding agent with your product open. Answer its questions, let it finish, then deploy your product as you always do.
   - Working with a developer instead? Send them the brief and the address.
5. **Check the connection** — Control Tower asks your product the way it will from now on. It says **Connected**, or what to fix, in plain words:
   - *cannot reach* — the address is wrong, or your product is not running there (a product on your own computer cannot be reached from the server);
   - *sends visitors on to another address* — use that address instead;
   - *has nothing at …/manifest* — the new part is not deployed yet, `CONTROL_TOWER_SECRET` is not set in your product (it switches the part on), or it lives under another path;
   - *answers without checking who is asking* — not safe: your coding agent must add the signature check;
   - *refuses Control Tower's signature* — the secrets differ: put the secret in again and restart your product, or make a new one;
   - *what it offers is not right* — give that sentence to your coding agent.
6. **Switch it on and make your first agent** — its starter agents are offered in Team → **New agent**.

## Good to know

- Nothing changes in your product unless you clear a suggestion; most connections are read-only.
- Fields your product marks personal (e-mail, name, phone …) never reach an agent.
- The plain-language guide for the whole of Control Tower is `docs/GUIDE.md` in the code.

Every step here is recorded in the [Admin log](docs:admin-log).
