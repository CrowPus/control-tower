"""Who is on the team: each agent's job, the events it reacts to, the tools it may be given
and the actions it may propose.

The core has one built-in agent, Nia, the owner's chief of staff. Every other agent is the
owner's own, made from the agent template (stage CT2) or brought in from outside
(services/outside). This is the team's design, not its settings: whether an agent runs, how
often, with which model and budget, which of its tools it actually gets and how much it may
do alone are the owner's settings (`agents` in the platform settings, Admin → Team).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Level = Literal["L1", "L2", "L3"]

@dataclass(frozen=True)
class ActionType:
    key: str
    label: str
    # L1: the owner approves each one, and may later let it run alone within limits (L2).
    # L3: always the owner (money, prices, contracts, deletions, the agents' own instructions).
    ceiling: Literal["L2", "L3"]
    # The shape of payload_json the executor expects, for the agent's instructions.
    payload: str = ""


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    department: str
    purpose: str
    # The Insights metric the owner judges it by, when there is one.
    kpi: str = ""
    subscribes: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    # How this agent works, beyond the team's rules (part of its design, not the owner's playbook).
    guide: str = ""
    # Rounds of tool use in one run, when its work needs more than the team's default (runner.MAX_ROUNDS).
    max_rounds: int = 0


# Every agent may read the numbers, the team's events and its shared brain, keep notes and propose a change to
# its own playbook; the rest comes with its stage.
COMMON_TOOLS = ("read_insights", "read_events", "search_brain", "keep_note", "emit_event", "propose", "report_directive")
COMMON_ACTIONS = ("playbook.change",)

ACTIONS: dict[str, ActionType] = {
    a.key: a
    for a in (
        ActionType("playbook.change", "Change the agent's own playbook", "L3", '{"text": "the whole new playbook", "note": "what changed"}'),
        ActionType("product.idea", "A change to the product, with the number it should move (measured before and after)", "L2",
                   '{"title": "short", "problem": "what members run into, with the numbers", "idea": "the change, concrete enough to build", '
                   '"evidence": ["each number or member words it rests on"], "metric": "an Insights number key", "expect": "up|down", "measure_days": 14}'),
        ActionType("social.post", "A social media post (published by the service after approval)", "L2",
                   '{"networks": ["instagram", "facebook", "linkedin", "youtube", "x"], "kind": "text|image|carousel|video", "text": "the post as most networks get it", '
                   '"texts": {"linkedin": "a version for one network, when it differs"}, "media_ids": [1], "link": "https://… the company’s own address", '
                   '"title": "YouTube title (video only)", "publish_at": "2026-10-08T09:00 (optional; else the owner\'s next posting hour)", '
                   '"video_script": {"hook": "the first 2 seconds", "length_seconds": 30, "scenes": [{"seconds": "0-3", "shot": "what the camera shows", "on_screen_text": "…", "voiceover": "…"}], "music": "mood", "call_to_action": "…"}}'),
        ActionType("video.concept", "A video concept with the company's character (made only after the owner clears it)", "L3",
                   '{"title": "short", "series": "the recurring format it belongs to (optional)", "idea": "the concept in 2-3 sentences", '
                   '"why_it_works": "the audience insight and the format or trend it uses", "audience": "who it is for", "hook": "what we see and hear in the first 2 seconds", '
                   '"setting": "ONE place, kept in every scene", "outfit": "what the character wears in this video, kept in every scene", "uses_character": true, '
                   '"scenes": [{"seconds": 8, "start_frame": 1, "shot": "camera, framing, light (steady camera)", "action": "ONE simple action", '
                   '"expression": "the character\'s face and body in this moment (big and clear)", "sound": "music mood and sound effects", '
                   '"dialogue": "ONLY when character.speaks is true: the exact English words (at most 20 per 8 seconds)"}], '
                   '"end_card": {"headline": "max 60 characters", "subline": "max 90 characters"}, "caption": "the post text, English, hook first, call to action", '
                   '"hashtags": ["#..."], "networks": ["tiktok", "instagram", "facebook"], "link": "the company’s own address (optional)", '
                   '"facts_used": ["each statement about the company in the video, copied from brand_kit"]}'),
        ActionType("marketing.plan", "The Marketing Lead's campaign plan for a period", "L3",
                   '{"title": "…", "period_start": "YYYY-MM-DD", "period_end": "YYYY-MM-DD", "summary": "the plan in five sentences", '
                   '"goals": [{"metric": "new members from Ghana", "target": 40, "baseline": 5}], "audiences": [{"name": "…", "who": "…", "where": "the countries", "why_now": "the open opportunities behind it"}], '
                   '"campaigns": [{"name": "…", "audience": "…", "channels": ["instagram", "facebook", "tiktok"], "message": "…", "content": ["3 posts a week: …"], "budget_note": "…"}], '
                   '"briefs": {"content_community": "what Social Media makes this period", "graphic_designer": "the video themes and series this period"}, '
                   '"measures": ["how success is read: sign-ups, reach, engagement"]}'),
        ActionType("admin.change", "Change something in the admin, exactly as shown", "L3",
                   '{"method": "PUT", "path": "/admin-api/...", "query": {}, "body": {"as admin_endpoints describes it": "..."}}'),
    )
}

_FACTS_ONLY = "Facts only from your tools; never invent numbers, names or results; say what you do not know."

_AGENTS = (
    Agent("nia", "Nia", "Leadership", "The owner's chief of staff: the complete rundown, the owner's directives to the team, the memory of decisions and lessons, meetings and presentations.",
          tools=("confirm_directive", "team_overview", "agent_report", "approvals_waiting", "decide_approvals", "read_memories", "remember", "propose_directive",
                 "read_admin_docs", "admin_endpoints", "propose_admin_change", "social_overview", "social_results", "marketing_plans",
                 "browse", "browser_status", "browser_answer",
                 # Running the team for the owner: every agent's picture, the admin's settings, starting and asking agents.
                 "agent_profile", "read_settings", "run_agent", "ask_agent",
                 # Every desk seen by Nia herself, so the owner never has to open each page.
                 "health_numbers", "ops_signals", "incidents", "qa_history", "product_ideas", "content_calendar", "video_studio",
                 "web_search", "read_web_page"),
          actions=("admin.change",), max_rounds=16,
          guide=("You speak for the whole team. A rundown follows this order: where the company stands (the numbers that matter, with the change), "
                 "what changed since the last one, what needs the owner (proposals waiting for clearance, settings agents wait for, risks), then each "
                 "agent in one or two sentences. Answer from your tools and memory; say when you do not know. When the owner gives an instruction for "
                 "the team, call propose_directive and read it back in one sentence; it reaches the agents only after the owner confirms. You have a real "
                 "memory: who the owner is, what you did together and what is open (see WHAT YOU REMEMBER and WHERE YOU LEFT OFF). Greet the owner as they "
                 "like to be addressed and pick up where you left off. When the owner decides something, tells you how they like things or something about "
                 "themselves, or when you finish a piece of work together, remember it (with a short topic). Never put a customer's personal data into memory. "
                 "When the owner asks you to change something in the admin, read how it works (read_admin_docs), look up the exact request "
                 "(admin_endpoints), then propose_admin_change with the complete body; say it waits in Clearance and happens when the owner clears it on "
                 "screen. YOU RUN THE TEAM FOR THE OWNER: the owner talks to you, not to each agent. Before you change an agent or a setting, read "
                 "it (agent_profile for an agent, read_settings for a setting) and send the whole object with only the owner's change in it. A "
                 "PLAYBOOK is the owner's standing instructions to one agent on top of its job: when the owner asks for one, read agent_profile, write "
                 "the complete playbook (what to focus on, what to avoid, how to report, the owner's rules; never against the facts-only rule), show it "
                 "to the owner in full, and after their yes propose_admin_change PUT /admin-api/team/agents/<key>/playbook with {text, note}. To have an "
                 "agent work now: run_agent. For an agent's view: ask_agent with the owner's question, and pass on its answer with its name. THE "
                 "BROWSER: when the owner asks you to do something on the web, call browse with the goal in full — every detail the owner gave — and the "
                 "address if known; tell the owner they can watch on the Nia page. Follow it with browser_status. When the browser asks (status asking), "
                 "read the question back and pass the owner's answer with browser_answer only after the owner said it (allow, take_over, stop, or reply "
                 "with their words). Passwords are always typed by the owner. When it is done, give the owner the report in two or three sentences. "
                 "Never claim you did something no tool did. " + _FACTS_ONLY)),
)

AGENTS: dict[str, Agent] = {a.key: a for a in _AGENTS}


def departments() -> list[str]:
    """The departments the agents work in, in the order they first appear (the owner names them in each agent)."""
    return list(dict.fromkeys(a.department for a in AGENTS.values()))


def get(key: str) -> Agent | None:
    return AGENTS.get(key)


def tools_of(agent: Agent) -> tuple[str, ...]:
    return COMMON_TOOLS + agent.tools


def actions_of(agent: Agent) -> tuple[str, ...]:
    # An outside agent (services/outside) proposes only what the owner gave it — never a change to its own playbook.
    common = () if agent.key.startswith("ext_") else COMMON_ACTIONS
    return tuple(a for a in common + agent.actions if a in ACTIONS)
