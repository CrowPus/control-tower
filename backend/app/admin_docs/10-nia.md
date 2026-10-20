key: nia
title: Nia
order: 10
screens: /nia, /nia/memory, /nia/meetings/:id, /nia/browser
api: /admin-api/nia
summary: Your chief of staff — chat and voice, the brief, directives to the team, what Nia remembers, meetings with a prepared deck and minutes, and Nia's browser.
---
## What it is for

Nia speaks for the whole team. Ask her for the rundown, about any agent or number, or tell her what the team should do. She answers from the agents' work, the numbers in Insights, what she remembers about your decisions, and these Docs: ask her "how does the Clearance page work?" and she explains it from the same text you read here (she needs the tool **read admin docs** ticked in [Team](docs:team) → Nia).

## Talking to Nia

- **The round Nia button** (bottom right, on every page) opens a chat. **Talk** starts a voice conversation: just speak, and speak again to interrupt her.
- **Nia works the admin with you, like you.** While you talk, she can do everything you can do on the dashboard: open any page ("show me the team", "take me to Team → Nia"), read what is on it, open things ("open the e-mail from Alex"), type into fields, choose in lists, tick and untick ("untick X"). It happens on your screen, with your sign-in.
- **Nothing is saved without you.** The moment one of her actions would save or change something, a bar appears: **"Nia wants to save a change on this page — Allow / Cancel"**. Nothing is saved until you press Allow. Looking, opening and reading need no confirmation.
- She **reads your screen** to know what is there (the page's text and every button, field and checkbox with its value) instead of guessing. Only while you talk, only when she needs it.
- Typing in the chat while you talk goes into the same live conversation, so typed requests work the page too. Without a voice conversation, the chat answers from her tools and proposes changes for [Clearance](docs:approvals).
- The conversation stays on while you move between pages: a small **"Nia is listening"** bar at the bottom of every page shows what is said, with **Mute** and **End**.
- **Nia page → Conversation**: your conversations, newest first. **New conversation** starts a fresh one; **Open full page** opens the chat large.

## Nia runs the team for you

You talk to Nia; she works with the agents.

- **Playbooks.** "Write a playbook for the Marketing Lead" — Nia reads the agent (its job, built-in guide, tools, settings, current playbook and last runs), writes the complete playbook, shows it to you in full and, after your yes, proposes it in [Clearance](docs:approvals). It takes effect when you clear it.
- **Settings.** "Run the Marketing Lead every morning at 7" or "give the QA Runner a $3 daily budget" — Nia reads the current values first and proposes the whole setting with only your change in it, so nothing else is reset.
- **Start an agent now.** "Let the QA Runner check now" — the run starts within a minute; ask Nia for its result afterwards.
- **Ask an agent.** "Ask the Marketing Lead what worked last month" — Nia puts your question to the agent and brings back its answer. It also shows in that agent's chat (Team → agent → Chat), and anything it proposes waits in Clearance. It costs that agent's AI budget.
- **Every desk.** Health and incidents, QA, product ideas, the content calendar and the video studio — Nia reads them herself.

Settings that hold passwords or keys (mailboxes, the QA test account, the social networks' developer apps) are never shown to Nia, and secrets in other settings are hidden from her.

## Changes Nia proposes for later (Clearance)

Outside a voice conversation (in the text chat, or in her scheduled work), ask Nia to change something ("set the posting hours to 9 and 18", "switch the QA Runner off"). She reads how it works in these Docs, looks up exactly what the admin needs, and **proposes the exact change**. It waits in [Clearance](docs:approvals) as **Always you**:

- The card shows exactly what will be sent (for example `PUT /admin-api/success/rules` with the values).
- **Approve** — your own admin page sends it, with your own sign-in. The admin's usual rules apply (owner-only, validation), and the [Admin log](docs:admin-log) records it as Nia's change approved by you, with what came back.
- **Edit, then approve** — change the values first.
- **Reject…** — say why; Nia learns from it.

Nia never changes anything by herself, and these are never hers, also not with an approval: signing in, admin accounts, authenticator recovery, and deciding approvals. Her admin changes are approved one by one on screen — never in bulk, by voice or in chat.

In a voice conversation she then opens Clearance and points at the card for you.

## The tools Nia needs

Tick them in [Team](docs:team) → Nia → **Tools it may use**, then Save. Without a tool she says which one is missing.

| Tool | What it lets her do |
|---|---|
| read admin docs | Explain any admin page from these Docs. |
| admin endpoints | Look up exactly what an admin change needs. |
| propose admin change | Propose a change for your approval. |
| social overview, social results, marketing plans | Social posts and what they brought. |
| team overview, agent report, approvals waiting, read insights | The team, the numbers and what waits for you. |
| decide approvals | Decide proposals you name by voice or chat (never "Always you" ones). |
| read memories, remember, propose directive | Her memory and your instructions to the team. |
| agent profile, read settings | Read an agent or a setting before she proposes a change (playbooks, schedules, budgets). |
| run agent, ask agent | Start an agent now; put your question to an agent. |
| health numbers, ops signals, incidents, qa history | Is the install running well. |
| product ideas | Ideas and what they changed. |
| content calendar, video studio | Posts and videos. |
| web search, read web page | Look something up on the web quickly. |

## Latest brief

Nia's written rundown of what happened and what needs you. She writes it on the schedule set for her in [Team](docs:team) (Team → Nia), or now with **Brief me now** (it appears within a minute).

## Directives

When you tell Nia what the team should do ("No paid workshops in the catalogue"), she turns it into a **directive** and shows it as **Waiting for your OK**:

- **Confirm** — it goes to the agents it concerns.
- **Correct** — change the wording first.
- **Cancel** — drop it.
- **Mark done** — close a directive that is finished.

Nothing reaches the agents before you confirm.

## What Nia remembers (Nia → Memory)

Nia has a real memory, the same in chat, by voice and in the browser.

- **About you**: your name, how Nia addresses you, your role and what she should know about you. You write it; Nia never guesses a name. Until it is filled in, Nia asks how you would like to be addressed.
- **Where you left off**: when you start a new conversation or talk by voice, Nia sees the end of your last conversation and picks up from there instead of asking what she can do.
- **Her journal**: about 10 minutes after a conversation goes quiet, Nia writes down what you did together, what was decided, what you told her about yourself and what is still open. Every finished browser task is kept the same way.
- Six kinds: **About you**, **Decision**, **Preference**, **Work we did**, **Lesson** (from results, with evidence) and **Fact**.
- Every memory has a **topic** (website, marketing, finance, a customer group …). **Branches** shows every topic with how many memories it holds — click one to see only that branch.
- **Tell Nia to remember**: choose the kind and topic, write the text and optionally the evidence, then **Remember**. In a conversation you can also say "remember that …".
- **Search**, the **kind filter** and the branches find entries; **Correct** edits one (also its topic); **Forget** deletes it.

## Meetings

- **Schedule**: a **Title**, **When**, an optional **Agenda**, **Every week at this time** for a repeating meeting, and **Prepare hours before**: when Nia starts preparing the deck.
- Before the meeting, Nia prepares a **deck** (slides with the numbers and what needs decisions).
- **Present**: Nia presents the deck by voice, slide by slide; talk to interrupt her or ask questions. **Previous / Next slide** move by hand.
- Afterwards Nia writes the **Minutes**: decisions and to-dos. The deck and minutes can be downloaded as PDF.
- **Earlier meetings** keeps past meetings.

## Nia's voice (on the Nia page)

| Setting | Meaning |
|---|---|
| **Voice** | Which voice Nia speaks with. Not set: voice is off. |
| **Talked today** | The minutes of voice conversation today. There is no daily limit: Nia is your own assistant. |

## Nia's browser (Nia → Browser)

Nia can work in a real web browser for you — look something up, compare offers, fill in a form, check a portal — while you watch. Tell Nia by voice or in the chat ("Nia, find the opening hours of …", "fill in … on …"), or write the task on **Nia → Browser**.

- **You watch live.** While Nia works, the screen shows on the Nia page and on Nia → Browser, with each step written below it. Nia works **only while you watch**: close the page and Nia pauses; open it again and Nia goes on.
- **Nia asks first** before anything that sends, submits, buys, pays, signs, accepts terms, deletes, uploads or signs in. You answer **Allow**, **Take over** or **Stop** — on screen or by voice. The screen at that moment is kept with the session.
- **Passwords are yours.** When a page needs a password, PIN or certificate, Nia hands the browser to you. You click on the screen and type in the box below it (Enter, Tab and the arrows are there too); what you type is never written down. Then **Hand back to Nia**, optionally saying what you did.
- **Take over any time**: click on the screen or press **Take over**. **Pause**, **Go on** and **Stop** are always there.
- **Words on a web page are never instructions** to Nia. A page that tells Nia to do something else changes nothing.
- When Nia is done, the report is kept with the session under **Earlier sessions**.

**Rules** (Nia → Browser): switched on or off; the most minutes and steps per session; **sites Nia may never open** (e.g. your bank — their subdomains too); **sites that stay signed in** after a session (everything else is forgotten when a session ends). Internal addresses of the install's own servers can never be opened.

Tick **browse**, **browser_status** and **browser_answer** for Nia in [Team](docs:team) so Nia can start and follow the browser when you talk to her.


## Set up Nia

Nia needs her settings on [Team](docs:team) (Team → Nia): switched on, a model, a daily AI budget and the brief schedule. Until then the Nia page shows **Set up Nia**.

## When Nia says something is done

Nia says something is done only when a tool shows it. Her proposals wait for your clearance in [Clearance](docs:approvals); her directives reach the agents only after you confirm them (on screen, or by saying yes when she reads one back — she then confirms it with **confirm_directive**, which you tick in Team → Nia). Before every answer she sees what really became of her recent proposals and directives (waiting, done, or failed and why), and after you say you approved something she checks with **proposal_status** (every agent always has it) before she confirms. A change she proposes in the admin is checked first: the admin must accept its body, and a change that replaces a whole setting must contain every part of it, so nothing you set is reset by accident. When an approved proposal fails, the bell tells you.
