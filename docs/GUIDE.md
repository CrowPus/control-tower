# Control Tower — the guide

For the person who runs the business. You do not need to be a developer. Where a step needs code, this guide says so,
and tells you exactly what to give a developer or your coding agent (Claude Code, Cursor, Copilot, Lovable, …).

## What Control Tower is, in one minute

Think of an office next to your product.

- **Your product** is what you built: an app, a shop, a website. Control Tower does not replace it or change how it
  works.
- **Control Tower** is the office, on your own server. In it work **agents**: AI workers you create. Each has a job
  ("watch our sign-ups and tell me what moves", "check failed payments every morning").
- An agent can **look** at your product (numbers, lists) and can **suggest** doing something. It can never do
  anything on its own: every suggestion waits in **Clearance** until you say yes or no.
- **Nia** is already there: your chief of staff. Ask her anything about the office, by text or voice.

The office learns about your product through a **connection** (a "plugin"). Without one, agents can only work with
what is inside Control Tower and the web.

## The words you will see

| Word | What it means |
| --- | --- |
| **Agent** | An AI worker you made, with one job. It starts switched off. |
| **Starter template** | A ready job description you can copy and change, so you do not start from nothing. |
| **Tool** | Something an agent may look at: "the newest users", "failed payments of the last 7 days". You tick which tools each agent gets. |
| **Action** | Something an agent may *suggest* doing: "run this failed job again". Nothing happens until you clear it. |
| **Clearance** | Your inbox of suggestions. Approve or reject each. Some (money, deleting, settings) always need you. |
| **Plugin / connection** | How Control Tower talks to your product. It also gives agents its tools, actions, numbers and starter agents. |
| **Numbers / Insights** | Your product's numbers (sign-ups, orders …) and the agents' work and costs, day by day. |
| **Event** | Your product telling the office something just happened ("someone signed up"), which can wake an agent. |
| **Budget** | How much AI an agent, or the whole team, may use per day. When it is used up, work stops until tomorrow. |
| **Shared secret** | A long password Control Tower and your product both keep, so each knows the other is real. |

## Your first hour

### 1. Install (about 10 minutes)

On a Linux server with Docker — or ask whoever runs your server:

```bash
git clone https://github.com/CrowPus/control-tower.git
cd control-tower
./install.sh
```

It asks five questions, then prints **a link**. That link is how you get in: open it, choose a password, and add an
authenticator app (Google Authenticator, 1Password, …) — every sign-in needs both. Lost the link, or it says
"invalid"? On the server: `docker compose exec app /app/backend/.venv/bin/python -m app.admin_cli invite-again --email you@example.com`.
Copy a link by clicking or selecting it, never by retyping: `l`, `I` and `1` look alike.

### 2. Getting started (about 10 minutes)

After you sign in you land on **Getting started**. Do the three needed steps:

1. **Your company** — a few sentences in your own words: what you do, for whom. Every agent reads this first, so be
   concrete ("We sell handmade lamps to small hotels in Europe").
2. **The AI** — pick Gemini, Claude or OpenAI and paste your key from that provider. Press **Test**. Prices are
   filled in for Gemini; for others, copy them from the provider's price page — budgets count with them.
3. **The team** — switch it on, choose the language agents write to you in, and a daily budget (start small: $2).

### 3. Connect your product (10 minutes to an afternoon)

Go to **Plugins → Connect your project**. It asks what your product is and shows the way that fits:

| Your situation | The way | Who does the work |
| --- | --- | --- |
| Payments run on **Stripe** | Switch on the Stripe plugin, paste a read-only key | You, 5 minutes |
| You only want numbers from your **database** (PostgreSQL, MySQL) | Switch on the Database plugin, paste a read-only address, write the questions | You or a developer, 30 minutes |
| Your product is an **app or website in any language** | Connect over HTTP: your product learns to answer Control Tower | Your coding agent or a developer, an afternoon |
| You write **Python** and can put code on the Control Tower server | A Python plugin | A developer |

For the app-or-website way, Control Tower writes **the exact message for your coding agent** and gives you **the
brief** it needs (a file). You:

1. open your product's code in your coding agent,
2. save the brief into your product's folder and paste the message,
3. answer its questions (what should agents see?), let it build, and deploy your product as you always do,
4. put the shared secret into your product's settings (the agent tells you where),
5. press **Check the connection**. It says "Connected" — or tells you in plain words what to fix.

Then switch the plugin **on**.

### 4. Your first agent (5 minutes)

**Team → New agent**. Pick a starter template (your product's own appear once it is connected and switched on) or
start blank. Change the job if you like, then **Make the agent**. On its page:

1. tick the **tools** it may use,
2. choose a **model** (fast is fine to start) and a **budget per day**,
3. set **when it works** (for example every morning at 8:00), or leave it to wake on events,
4. switch it **on**, and press **Run now** to see it work once.

Its report appears under *Recent runs*; its suggestions in **Clearance**.

## Day to day

- **Clearance** — look once a day. Approve, reject (say why — the agent learns from it), or edit before approving.
  After enough approvals in a row, an agent may offer to do a kind of action alone within a daily limit you set;
  money, deleting and settings are never offered.
- **Insights** — your product's numbers next to the agents' work and what it cost.
- **Talk to an agent** — the round button on every page: Nia, or any agent. Tell an agent "from now on…" and it
  suggests a change to its own instructions, which you clear.
- **Stop everything** — Platform → *Pause all automation*. One switch, everything stops.

## When something is wrong

| You see | What it means | What to do |
| --- | --- | --- |
| "This invitation is invalid or has expired" | The link was changed when copied, or is older than 72 hours | Make a new one with `invite-again` (above) and click it |
| An agent says **Waiting for settings** | It lacks a tool, a model, a budget or a schedule | Its page lists exactly what is missing |
| An agent never runs | No schedule and nothing woke it, or the team is off | Give it a time, or press Run now; check Team → the team switch |
| "Today's AI cap is used" | The install's or the team's budget for today is spent | Wait until tomorrow, or raise the budget (Team, Platform) |
| **Check the connection** says it cannot reach your product | Wrong address, or your product is not deployed with the new code | Open the address it shows in your browser: "refused" means the address is right |
| …says "the secret does not match" | Your product's `CONTROL_TOWER_SECRET` differs from Control Tower's | Press New secret, put the new one in your product's settings, restart your product |
| …says the product "has no Control Tower addresses" | The code was not deployed, or lives under another path | Deploy again; check the address ends where your coding agent put the routes |
| A suggestion **failed** after you cleared it | Your product refused or could not do it | Clearance shows the reason; tell the agent or your developer |

## Safety, in plain words

- Agents only suggest. Your product changes only when you clear something (or within a daily limit you gave).
- What your product marks **personal** — e-mail addresses, names, phone numbers — is hidden from every agent.
- Keys you paste are stored encrypted. The admin needs your password **and** your authenticator.
- Everything is in the **Admin log**: who did what, when.
- Your data stays on your server. The only outside service is the AI provider you chose, which sees what an agent
  sends it while it works.

## Example: Zelyq

Zelyq (an AI app builder) was connected over HTTP. Its coding agent added a small part to Zelyq's server that answers
Control Tower; the owner set three settings in Zelyq (`CONTROL_TOWER_SECRET`, `CONTROL_TOWER_URL`,
`CONTROL_TOWER_ACTS_AS`) and pressed Fetch. Now agents can read Zelyq's sign-ups, projects, failed pipeline runs and
deployments; the **Zelyq Release Guard** agent suggests running a failed pipeline run again, which the owner clears;
and a new sign-up in Zelyq wakes the **Zelyq Growth Watch** agent. See Zelyq's `docs/control-tower.md`.

## More

- For developers and coding agents connecting a product: [CONNECT-A-PROJECT.md](CONNECT-A-PROJECT.md).
- The full technical reference for plugins: [PLUGINS.md](PLUGINS.md).
- Inside the admin: **Docs** (the book icon) explains every page, and Nia can answer questions about any of it.
