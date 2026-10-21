<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CrowPus/control-tower/main/docs/images/logo-dark.png">
    <img src="https://raw.githubusercontent.com/CrowPus/control-tower/main/docs/images/logo.png" alt="Control Tower" width="420">
  </picture>
</p>

# Control Tower

Your own AI team, on your own server.

**New here, and not a developer?** Read [the guide](docs/GUIDE.md): what it is in plain words, your first hour, and
how to connect your product — with your coding agent doing the code.

Control Tower is a self-hosted admin and agent office for founders. You install it next to your project, connect your
project through a plugin, and build the agents you want from one template: a name, a job, the tools they may use and
the actions they may propose. You decide what each agent is. Control Tower decides only one thing: every agent follows
the rules — it proposes, you give clearance, and nothing changes without it.

- **Your agents, your design.** No built-in team except Nia, your chief of staff. Make as many agents as you want,
  blank or from a starter template (Marketing Lead, Product Analyst, Researcher, Engineer on Duty, …).
- **Rules every agent follows.** Agents only propose; you clear (money, deleting and settings always need you);
  budgets per agent and for the team; no duplicate proposals; a full log; one switch stops everything.
- **Your project, connected.** Plugins give agents your project's records, actions and numbers — as a Python plugin
  or over HTTP from a project in any language. Stripe and a read-only database connection come ready to use.
- **Bring your own AI.** Google Gemini, Anthropic Claude or OpenAI (or any service speaking OpenAI's API) with your
  own key. Outside agents — Claude Code, ChatGPT and others — can join through the gateway (MCP).
- **Self-hosted.** One install is one company. Your data and keys stay on your server.

Also inside: Insights (your numbers, the agents' work and costs), Clearance, the team's shared Brain, a 3D Office,
a mail client for the agents' mailboxes, a marketing studio, and Nia by text or voice.

## Install

You need a Linux server with [Docker](https://docs.docker.com/engine/install/) and Compose v2. Control
Tower uses about 300 MB of memory when idle; building it needs about 2 GB free and 5 GB of disk.

```bash
git clone https://github.com/CrowPus/control-tower.git
cd control-tower
./install.sh
```

The installer asks for your time zone, the address you will open it at, your company's name, your e-mail and your
name. Then it:

1. writes `.env` with strong secrets (keep this file safe — without it, stored keys cannot be read),
2. makes the private certificate the database is reached with,
3. builds and starts the server, the worker, PostgreSQL and Redis,
4. prints a one-time link where you set your password and your authenticator app.

You can run it again at any time: it keeps your secrets and data. Without questions:
`./install.sh --email you@example.com --name "Your Name" --address https://tower.example.com --time-zone Europe/Berlin --yes`.

### Open it

Control Tower listens only on this machine (`127.0.0.1:8000`; another port with `APP_PORT` in `.env`).

- **Just trying it on your own computer:** use `--address http://localhost:8000` and open that address.
- **On a server, only for you:** an SSH tunnel — `ssh -L 8000:127.0.0.1:8000 you@your-server` — and open
  `http://localhost:8000` (install with that address).
- **On a server, at your own domain:** put a reverse proxy with HTTPS in front of it. With [Caddy](https://caddyserver.com),
  which gets the certificate by itself, the whole configuration is:

  ```
  tower.example.com {
      reverse_proxy 127.0.0.1:8000
  }
  ```

  Install with `--address https://tower.example.com`. To let only your own addresses open the admin, set
  `ADMIN_IP_ALLOWLIST` in `.env` (the agent gateway and the links projects use stay reachable).

### Getting started

The first time you sign in, **Getting started** walks you through what the team needs:

1. your company — what it does and for whom (every agent reads it),
2. the AI your team thinks with — Gemini, Claude or OpenAI, your key, the models and their prices, and a test,
3. the team's switch, language and daily budget,

then your first agent (Team → New agent) and your project (Plugins). Every agent starts switched off until you give it
its tools, budget and schedule.

E-mail (admin invitations, alerts, the Monday report) uses the SMTP settings in `.env`; without them, alerts appear in
the admin's bell.

## Connect your project

Admin → **Plugins → Connect your project** walks you through it and writes the message for your coding agent. Three ways:

- **Ready to use:** Stripe (read-only: subscriptions, payments, revenue) and Database (your PostgreSQL or MySQL,
  read-only, with the questions you write as SQL). Switch them on and fill in their settings.
- **Over HTTP:** your project — in any language, on any server — answers a few signed HTTPS requests. Press
  **Connect a project**, put the shared secret into your project, press **Fetch**.
- **A Python plugin:** a folder in `plugins/`, then restart (`docker compose restart app worker`).

For the coding agent or developer doing it: [docs/CONNECT-A-PROJECT.md](docs/CONNECT-A-PROJECT.md) (the brief) and
[docs/PLUGINS.md](docs/PLUGINS.md) (the reference), with a complete example in
[examples/plugins/hello_shop](examples/plugins/hello_shop/plugin.py).

## Backups and updates

```bash
./scripts/backup.sh            # database, files and settings into backups/ (add --media for the studio's videos)
./scripts/restore.sh backups/ct-20261020-030000
./scripts/update.sh            # backs up first; goes back by itself if the new version does not start
```

The installer shows the line that makes a backup every night. Backups are kept 14 days (`BACKUP_KEEP_DAYS`);
`BACKUP_COPY_COMMAND` in `.env` copies each one elsewhere (for example with rclone). A backup holds a copy of `.env`,
so keep backups as safe as `.env` itself.

## Optional parts

- **Nia's browser** (Nia browses the web for you, live-watched): `docker compose --profile browser up -d`.
- **Virus scanning** of files attached in chats and mail: `docker compose --profile clamav up -d`, then
  `CLAMD_HOST=clamav` in `.env` (needs about 1.5 GB of memory).
- Pictures, videos, Nia's voice, Nia's browser and web search use Google Gemini, whichever provider your team
  thinks with: add a Gemini key for them in Platform → AI.

## Develop

```bash
cd backend && uv sync --all-extras && cd ..
npm install
cp .env.example .env            # EXECUTION_MODE=development, DATABASE_ENGINE=sqlite; set ENCRYPTION_SECRET
cd backend && uv run python -m app.prestart
uv run python -m app.admin_cli create-owner --email you@example.com --name "Your Name"
uv run uvicorn app.main:app --port 8000 &
cd .. && npm run dev            # the admin on http://127.0.0.1:5174
```

Tests: `cd backend && uv run pytest`. Notes for contributors and coding agents: [AGENTS.md](AGENTS.md). The plan and
what was built: [docs/PLAN.md](docs/PLAN.md), [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

## License

Open source under the [GNU AGPL-3.0](LICENSE). Run it for yourself and your business; if you offer a modified version
to others over a network, share your source — or ask for a commercial license: **support@dee-empire.com**.
Plain-language summary: [LICENSING.md](LICENSING.md). Contributions under the [CLA](CLA.md).
