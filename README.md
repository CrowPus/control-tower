# Control Tower

Your own AI team, on your own server.

Control Tower is a self-hosted admin and agent office for founders. You install it next to your
project, connect your project through a plugin, and build the agents you want from one template:
a name, a job, a brain, the tools they may use and the actions they may propose. You decide what
each agent is. Control Tower decides only one thing: every agent follows the rules — it proposes,
you give clearance, and nothing changes without it.

- **Your agents, your design.** No built-in team: as many agents as you want, made from the
  template or copied from a starter template.
- **Rules every agent follows.** Proposals, never direct changes; levels of clearance; budgets;
  a full log; a stop switch.
- **Your project, connected.** Plugins give agents your numbers, your records and the actions
  your project allows.
- **Bring your own AI.** Gemini, Claude or OpenAI with your own key, or connect Claude Code,
  ChatGPT and other agents from outside through the gateway (MCP).
- **Self-hosted.** One install is one company. Your data and keys stay on your server.

Status: early. The core runs (stage CT1 of [docs/PLAN.md](docs/PLAN.md)); the agent template, plugins and the
installer come next. See [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

## Try it (development)

```bash
cd backend && uv sync --all-extras && cd ..
npm install
cp .env.example .env            # set ENCRYPTION_SECRET; GEMINI_API_KEY for the agents to think
cd backend && uv run python -m app.prestart
uv run python -m app.admin_cli create-owner --email you@example.com --name "Your Name"   # prints your sign-in link
uv run uvicorn app.main:app --port 8000 &
cd .. && npm run dev            # the admin on http://127.0.0.1:5174
```

## License

Open source under the [GNU AGPL-3.0](LICENSE). Run it for yourself and your business; if you
offer a modified version to others over a network, share your source — or ask for a commercial
license: **support@dee-empire.com**. Plain-language summary: [LICENSING.md](LICENSING.md).
Contributions under the [CLA](CLA.md).
