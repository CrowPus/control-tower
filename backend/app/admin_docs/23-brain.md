key: brain
title: Brain (what the team knows)
order: 23
screens: /brain
api: /admin-api/brain
summary: The agent team's shared knowledge as one living graph — agents, handoffs, proposals, notes, Nia's memories and your directives, linked; explore it and ask it questions.
---
## What it is for

Each agent writes down what it learns (notes), proposes actions, sends handoffs to colleagues, and Nia remembers your decisions. **Brain** draws all of it as one web, so you see at a glance who works on what, which proposals several agents touched, and what the team already knows before you decide. Every agent reads the same brain with its **search brain** tool.

Nothing here is new data: the page is drawn from the database each time (and again every minute while it is open). It only reads; it changes nothing.

## What the dots are

| Colour | Kind | Linked to |
|---|---|---|
| Teal (large) | **Agent** | its department, the handoffs it sends and the ones that wake it, its tools, its proposals and notes |
| Violet | **Department** | its agents |
| Amber | **Handoff** (an event type) | the agent that sends it → the agents it wakes |
| Grey | **Tool** (hidden at first) | the agents you granted it to |
| Blue | **Proposal** | the agent that made it, the notes and directives that name it (#id) |
| Yellow | **Agent note** | the agent that kept it, the agents and proposals (#id) it names |
| Purple / magenta | **Nia's memory** and its **topic** | Nia |
| Red | **Directive** | the agents it is for, the proposals it names |

An agent that ran in the last 6 hours glows; one that is off or waiting for a setting is drawn hollow. Bigger dots have more links.

No person ever becomes a dot.

## Working with it

- **Hover** a dot: its links light up and the rest fades; particles run along the links.
- **Click** a dot (or a name in the list on the left): the panel on the right shows everything about it and every link, each clickable. **Open in admin** goes to the page where you work on it.
- **Show around** keeps one dot and everything two steps from it. The pill at the top right clears it.
- **Find in the brain** searches names and texts; the coloured buttons hide or show a kind.
- **Time window** (top right) keeps the proposals and handoffs of the last 7, 30 or 90 days. Agents, notes, memories and directives always show.
- The toolbar: zoom, fit everything, names on or off, hold the graph still, shake up the layout (lets dragged dots go), refresh now. Drag a dot to pin it.

## Ask the brain

Type a question in the bar at the bottom ("What does the team know about this festival?", "Which agents are waiting, and for what?", "What did the owner decide about scholarships?"). The brain finds the matching dots and their links and answers in a few sentences, naming each dot it used. Those dots light up with a white ring and the view moves to them; click a name in the answer to open it.

- Only the owner asks; it uses **Nia's model tier** and costs from **Nia's AI budget** and the team's (shown in AI usage as `agent.nia.brain`). Set Nia's model tier in [Team](docs:team) → Nia first.
- The answer only reads. It proposes nothing and changes nothing; every question is in the [admin log](docs:admin-log).

## The agents' search brain tool

On each agent's page in [Team](docs:team), tick **search_brain** to let it read the brain in its runs and chats: the other agents' notes, the proposals and your decisions on them, your directives and Nia's memories of your rules, decisions, lessons and facts. Nia's memories about you as a person and her diary of the work you did together stay with Nia. With the tool, an agent looks up what a colleague already knows before it works on a topic, so it does not repeat or contradict them.
