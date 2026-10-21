# Changelog

What changed in each release. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [Semantic Versioning](https://semver.org/). The plugin interface has its own version (`API_VERSION` in
`backend/app/plugin_api.py`): a plugin written for 1.x keeps working on every 1.x.

## [Unreleased]

## [0.1.0] — 2026-10-21

The first public release.

### Added

- **Agents from one template**: make as many as you need, blank or from eight starter templates; the rules every agent
  follows are checked when you save it. Nia, the chief of staff, comes built in.
- **Clearance**: every suggestion waits for the owner, with its reason, expected effect and evidence; money, deleting
  and settings always need the owner; earned autonomy within daily limits the owner sets.
- **Plugins**: connect a product as a Python plugin or over HTTP from any language (signed both ways); tools, actions,
  numbers, events and starter agents; personal fields hidden from agents. Ready to use: **Stripe** and **Database**
  (both read-only).
- **Connect your project**: a guided page with the message and the brief for the project's coding agent, and a
  connection check that says what to fix in plain words.
- **AI providers**: Google Gemini, Anthropic Claude or OpenAI (or an OpenAI-compatible service), with your own key.
- **Insights**, the team's **Brain**, the 3D **Office**, a mail client for the agents' mailboxes, a marketing studio,
  Trust reviews, and a gateway (MCP) for outside agents.
- **Install and operations**: `install.sh`, Getting started, backups, restore, and updates that roll back by themselves.
- **Docs**: the guide for owners, the connection brief for coding agents, the plugin reference.

[Unreleased]: https://github.com/CrowPus/control-tower/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CrowPus/control-tower/releases/tag/v0.1.0
