# Security Policy

## Reporting a vulnerability

**Please do not open a public issue.** Report privately through GitHub's
[Report a vulnerability](https://github.com/CrowPus/control-tower/security/advisories/new) form, which opens a draft
advisory only you and the maintainers can see — or write to **support@dee-empire.com**.

Include what you can: the affected version or commit, how to reproduce it, and the impact. You will get an
acknowledgement within 3 working days and an assessment within 10. We keep you updated through the fix and credit you
in the advisory unless you prefer otherwise.

## Supported versions

Control Tower is before 1.0: only the latest release (and `main`) receives security fixes. Update with
`./scripts/update.sh`, which backs up first.

## What to know before you deploy

Control Tower runs AI agents against your business. These are the properties it is built on, and the places where
the boundary is yours to keep.

### What leaves your server

Your data stays on your server, with one exception you choose: **the AI provider**. While an agent works, what it
reads through its tools — your product's numbers and lists, notes, the brain, its instructions — is sent to the
provider configured in *Platform → AI* (Google, Anthropic, OpenAI, or the OpenAI-compatible address you set). Fields a
plugin marks personal are removed before that. Pictures, videos, Nia's voice, Nia's browser and web search go to
Google Gemini.

### Agents propose; code acts

A model never acts directly. Its tools read, keep notes and *propose*; a proposal is typed and checked, waits for the
owner (or runs within a daily limit the owner granted for that kind of action), and is carried out once by
deterministic code. Money, deleting and settings always need the owner. Text from e-mails, web pages, tool results and
plugins is treated as information, never as an instruction. If you find a way for model output or outside text to
cause an action without that path, that is a vulnerability — please report it.

### The admin

- Every sign-in needs a password **and** an authenticator code; sessions are kept on the server and end when you sign out.
- Put it behind HTTPS (the installer's address and your reverse proxy) and, where you can, an IP allowlist
  (`ADMIN_IP_ALLOWLIST`). The agent gateway, plugin events and media links stay reachable; each checks its own
  credential, signature or token.
- Keys and passwords entered in the admin are encrypted with `ENCRYPTION_SECRET`. Keep `.env` and backups (which hold
  a copy of it) private; losing `ENCRYPTION_SECRET` makes stored keys unreadable.

### Plugins

- A **Python plugin** (`plugins/<key>/`) runs inside Control Tower with all of its rights. Install only plugin code you
  wrote or trust.
- An **HTTP connection** sees only what Control Tower sends it. Every request both ways is signed (HMAC-SHA256 over
  time, path and body) and valid for five minutes. Keep the shared secret out of code and chats; renew it if it leaks.
- The ready-made **Stripe** and **Database** plugins are read-only by design: use a restricted Stripe key and a
  database user that may only read.

### Outside agents

Agents from outside (through the gateway, MCP) get a credential you issue, only the tools you tick, and the same
clearance rules as the team. Revoke a credential on its page.

## Out of scope

- Findings that need someone who already has the owner's password and authenticator, or shell access to the server.
- The behaviour of the AI providers themselves.
- Denial of service by sending large volumes of traffic.
- Missing hardening headers without a demonstrated impact.
