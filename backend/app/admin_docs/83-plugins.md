key: plugins
title: Plugins
order: 83
screens: /plugins
api: /admin-api/plugins
summary: Your projects connected to Control Tower — what each gives the agents (tools, actions, numbers, starter agents), switched on by you, with its settings and its shared secret.
---
## What it is for

A plugin connects one of your projects — your app, shop or website — to Control Tower. It can give:

- **Tools** — what agents may read from the project (customers, orders, products …). You still tick on each agent's page which tools it really gets.
- **Actions** — what agents may propose to do in the project. Nothing happens until you clear it in [Clearance](docs:approvals), and each cleared action is carried out once. *Always you* actions can never run alone.
- **Numbers** — the project's own numbers in [Insights](docs:insights), collected every quarter hour.
- **Starter agents** — templates that suit the project, in Team → **New agent**.
- **Events** — the project can tell the team something happened (a sign-up, a failed payment), which wakes the agents waiting for it.

Fields the plugin marks **personal** (e-mail addresses, phone numbers, names …) are hidden from every agent, and from the views here, wherever they appear.

## Two ways to connect a project

- **Python plugin** — a folder in the server's `plugins/` folder. It appears here after the server restarts. It runs inside Control Tower with all of its rights, so install only plugin code you wrote or trust. Developers: see `docs/PLUGINS.md` in the code.
- **Over HTTP** — for a project in any language or on another server. Press **Connect a project**, give it a short key (like `shop`) and the project's address for Control Tower (for example `https://shop.example.com/control-tower`). Control Tower shows a **shared secret** once: put it in the project's settings. Then press **Fetch** — Control Tower reads what the project offers. Press **Fetch** again whenever the project changes what it offers.

Every request between Control Tower and the project is signed with the shared secret, both ways, and a signed request is only valid for five minutes.

## Each plugin

- **On / off** — a plugin starts **off**: its tools, actions and starter agents are not offered, and a call to it is refused. A proposal you cleared while it was on, but carried out after you switched it off, fails with that reason. Switching on is refused until its required settings are filled in (and, over HTTP, its shared secret is set).
- **Settings** — what the plugin needs, such as its database address or a key. Secrets are stored encrypted and never shown again; leave a secret empty to keep it.
- **Shared secret** — **New secret** makes a new one and shows it once; the old one stops working at once, so put the new one into the project straight away.
- **What it gives** — its tools, actions (with who decides), numbers and starter agents.
- **Views** — some tools can be opened here as a table, so you see what the agents see.
- **Collect numbers now** — reads its numbers at once instead of waiting for the next quarter hour. Problems with a number are listed under it.
- **Remove** (HTTP only) — disconnects the project; its numbers stay in Insights' history. A Python plugin is removed by taking its folder off the server.

## When a plugin cannot load

Plugins that could not load are listed at the top with the reason (code that fails, a key that is taken, an interface version this Control Tower does not speak). The rest load as usual.

Every change here is recorded in the [Admin log](docs:admin-log).
