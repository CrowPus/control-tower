# Getting help

**New, and not a developer?** Start with [the guide](../docs/GUIDE.md) — your first hour, the words Control Tower uses,
and a table of what to do when something is wrong. Inside the admin, **Docs** explains every page, and Nia answers
questions about any of it.

**A question, an idea, or "am I holding it wrong?"** → [Discussions](https://github.com/CrowPus/control-tower/discussions).
Questions asked there stay findable for the next person.

**Something is broken** → [open an issue](https://github.com/CrowPus/control-tower/issues/new/choose). The form asks
for what makes a report actionable: what you expected, what happened, your version and the logs.

**Connecting your product** → Plugins → *Connect your project* in the admin, and
[the brief for your coding agent](../docs/CONNECT-A-PROJECT.md). Still stuck? Open a *Connect a product or service*
issue with what *Check the connection* said.

**A security vulnerability** → do not open an issue. Follow [SECURITY.md](../SECURITY.md).

## Before you ask

- **Cannot sign in, or "invitation invalid":** make a new link with
  `docker compose exec app /app/backend/.venv/bin/python -m app.admin_cli invite-again --email you@example.com`.
- **An agent waits or never runs:** its page lists exactly which setting is missing; check the team switch and budget.
- **The AI does not answer:** *Platform → AI → Test* says what is wrong with the key, model or address.
- **The server does not start:** `docker compose ps` and `docker compose logs --tail 50 app worker`.
