key: product
title: Product
order: 37
screens: /product
api: /admin-api/product
summary: The Product Analyst's ideas with what they changed, the QA Runner's checks (what fails, history, rules, test accounts) and the Engineer on Duty's incidents.
---
## What it is for

Where product work is kept: the ideas agents propose (with the Insights number each should move, measured before and after), the checks of your pages, and incidents. Agents with the product tools — for example a Product Analyst, a QA Runner and an Engineer on Duty made from the starter templates — work here; you can use every part yourself too.

## Ideas

An idea an agent proposed reaches you in [Clearance](docs:approvals). Once you clear it, it appears here as **To build**, with its number **Before** (the baseline, measured at once).

- **It is live now**: press it when the change is built and released. The measuring days start.
- After the measuring days the agent reads the same number again and writes the verdict: **Better**, **Worse**, **No change** or **Too little data**, with a note.
- **Drop**: the idea is not built.

## Checks

The checks read only; nothing is created or changed:

| Group | What is checked |
|---|---|
| **Pages** | The admin, and your pages: each answers, and contains your text when you gave one |
| **Security** | Security headers of your pages; each certificate and the days until it ends (production only) |

**Run now** runs them at once. **Failing now** shows checks that failed in a row, **Flaky** those that fail now and then. **Recent runs** keeps 30 days.

**Rules and pages** (owner only, in the [admin log](docs:admin-log)):

- **Run the checks by themselves every (minutes)**: the service runs them on this interval without any AI cost. Empty: only when an agent runs them or you press **Run now**.
- **A check counts as broken after (failed runs in a row)**: then agents that listen for `qa.failed` are woken. Empty: after the first.
- **Slow above (ms)**, **Warn when a certificate ends within (days)**: empty means not used.
- **Your pages**: a name, the address, and optionally a text the answer must contain (at most 30).

## Incidents

An agent (or you) writes down anything that hurts people: what is broken, its severity (**critical**: people cannot use the product; **major**: a feature is broken or a background job keeps failing; **minor**), who is affected since when, the likely cause and the fix a developer should make, and a timeline of notes. A new critical or major incident rings your bell and goes to the team's alert address.

You can add a note, set **Monitoring** (the symptom stopped) or **Resolved**, and write down an incident yourself.
