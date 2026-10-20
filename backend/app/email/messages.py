"""What every e-mail the install sends says (layout.py decides how it looks). English; a
translation is a second language key next to "en".

Placeholders: {app}, {name}, {link}, {base} and the extras a sender passes. [[...]] is emphasis.

AUDIENCE says whose e-mail it is: it sets the label in the header and the line in the footer
that says why the person receives it.
"""

from __future__ import annotations

from .layout import Audience, Message

AUDIENCE: dict[str, Audience] = {
    "admin_invitation": "admin", "admin_attention": "admin", "insights_weekly": "admin",
}


def audience(kind: str) -> Audience:
    return AUDIENCE.get(kind, "admin")


_ADMIN_INVITATION = Message(
    subject="Your {app} admin account",
    eyebrow="Administration",
    heading="You are invited to administer {app}",
    paragraphs=("You were invited as an administrator of {app}. Set your password to finish your account.",
                "At your first sign-in you set up an authenticator app; it is required at every sign-in."),
    button=("Set your password", "{link}"),
    note="The link is valid for 72 hours and works once.",
)
_INSIGHTS_WEEKLY = Message(
    subject="{app}: your week {week}",
    eyebrow="Weekly report",
    heading="How {app} did last week",
    paragraphs=("Last week's numbers, with the change against the week before. Charts and details are in the admin under Insights.",),
    facts=(("Agent runs", "{agent_runs}"), ("Proposals made", "{proposals}"), ("Cleared", "{cleared}"), ("AI cost", "{ai_cost}"),
           ("Server errors", "{server_errors}")),
    button=("Open Insights", "{link}"),
    greet=False,
)
_ADMIN_ATTENTION = Message(
    subject="[{app} admin] {what}",
    eyebrow="Needs attention",
    heading="{what}",
    paragraphs=("Something in {app} needs an administrator.",),
    button=("Open in the admin", "{link}"),
    greet=False,
)

MESSAGES: dict[str, dict[str, Message]] = {
    "admin_invitation": {"en": _ADMIN_INVITATION},
    "admin_attention": {"en": _ADMIN_ATTENTION},
    "insights_weekly": {"en": _INSIGHTS_WEEKLY},
}
