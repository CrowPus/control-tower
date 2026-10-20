"""Every e-mail the install sends is branded HTML with a plain-text alternative made
from the same words (email/layout.py, email/messages.py).

- every kind renders, with nothing left unfilled;
- the button and the text carry the same link, and only web links become buttons;
- whatever people typed (names, alert subjects) is escaped, and a name that looks like a
  link is not used at all;
- the message is multipart/alternative: text first, HTML second;
- the footer names the install, links its privacy page, and says why it arrives; no person,
  no postal address unless the owner set them.
"""

from __future__ import annotations

import re
from email import message_from_bytes

import pytest

from app.email import smtp
from app.email.messages import MESSAGES, audience
from app.services import system_mail

EXTRA = {"what": "An alert needs you", "week": "2026-09-28 – 2026-10-04",
         **{key: "12 (+20 %)" for key in ("agent_runs", "proposals", "cleared", "ai_cost", "server_errors")}}
LINK = "https://tower.example/accept-invite?token=abc123"


@pytest.mark.parametrize(("kind", "locale"), [(k, lang) for k in sorted(MESSAGES) for lang in sorted(MESSAGES[k])])
def test_every_mail_renders_completely(kind: str, locale: str) -> None:
    mail = system_mail.compose(kind, to="rita@example.com", name="Rita Radius", locale=locale, link=LINK, base="https://tower.example", extra=EXTRA)
    for part in (mail.subject, mail.body, mail.html):
        assert re.search(r"\{[a-z_]+\}", part) is None, f"{kind}/{locale}: a placeholder was left unfilled"
        assert "[[" not in part and "]]" not in part
    assert mail.html.startswith("<!doctype html>") and 'lang="' in mail.html
    message = MESSAGES[kind][locale]
    if message.button:
        href = re.search(r'<a href="([^"]+)" target="_blank" style="display:inline-block', mail.html)
        assert href, f"{kind}: no button"
        assert href.group(1).replace("&amp;", "&") in mail.body, "the text carries the button's link"
        assert 'v:roundrect' in mail.html, "Outlook gets its own button"
    assert "Dee Empire" not in mail.body and "Epliv" not in mail.body and "Epliv" not in mail.html
    assert "https://tower.example/privacy" in mail.html
    if message.greet:
        assert "Rita Radius" in mail.body


def test_people_typed_text_is_escaped() -> None:
    mail = system_mail.compose("admin_attention", to="owner@example.com", name="<b>Hanna</b>", locale="en", link=LINK, base="https://x.example",
                               extra={"what": '<script>alert(1)</script> "server down"'})
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html
    assert "<script>alert(1)</script>" in mail.subject, "the subject is plain text: it shows it as typed (it is never interpreted)"
    invited = system_mail.compose("admin_invitation", to="x@example.com", name="<b>Hanna</b>", locale="en", link=LINK)
    assert "<b>Hanna</b>" not in invited.html and "&lt;b&gt;Hanna&lt;/b&gt;" in invited.html


def test_a_name_that_looks_like_a_link_is_not_used() -> None:
    mail = system_mail.compose("admin_invitation", to="x@example.com", name="Visit evil.example now", locale="en", link=LINK)
    assert "evil.example" not in mail.body and "evil.example" not in mail.html
    assert "Hello,\n" in mail.body


def test_only_web_links_become_buttons() -> None:
    mail = system_mail.compose("admin_invitation", to="x@example.com", name="Rita", locale="en", link="javascript:alert(1)")
    assert "javascript:" not in mail.html and "javascript:" not in mail.body
    assert "v:roundrect" not in mail.html


def test_the_footer_says_who_and_why() -> None:
    mail = system_mail.compose("admin_invitation", to="m@example.com", name="Ali", locale="en", link=LINK)
    assert "You receive this e-mail as an administrator of Control Tower." in mail.body and "Owner:" not in mail.body
    assert "Control Tower · Control Tower" not in mail.body and "Questions? Write to us at support@tower.example" in mail.body
    assert audience("admin_invitation") == "admin" and audience("insights_weekly") == "admin"


def test_the_message_is_text_with_an_html_alternative() -> None:
    cfg = smtp.SmtpConfig(host="smtp.example.com", port=587, username="", password="", from_address="info@tower.example", use_tls=True)
    mail = system_mail.compose("admin_invitation", to="rita@example.com", name="Rita", locale="en", link=LINK)
    built = smtp.build_message(cfg, smtp.OutboundMail(to=["rita@example.com"], subject=mail.subject, body_text=mail.body, body_html=mail.html), sender_name="Control Tower")
    parsed = message_from_bytes(built.as_bytes())
    assert parsed.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in parsed.get_payload()] == ["text/plain", "text/html"]
    assert parsed["From"] == "Control Tower <info@tower.example>"


def test_an_unknown_language_falls_back_to_english() -> None:
    mail = system_mail.compose("admin_invitation", to="rita@example.com", name="Rita", locale="de", link=LINK)
    assert mail.subject == "Your Control Tower admin account"
