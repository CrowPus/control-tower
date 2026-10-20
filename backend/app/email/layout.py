"""How every e-mail the service sends looks: one branded layout, HTML and plain text
from the same content (services/system_mail.py holds the content).

The HTML is built for real mail clients: tables, inline styles, a 600 px column,
web-safe fonts, a bulletproof button (VML for Outlook on Windows), a hidden
preheader, light colours only (no client re-colours it badly) and alt text for the
logo, so it reads well with images blocked. Every value that comes from people
(names, job titles, companies) is escaped; links are only ever http(s).

Content strings may mark words with [[...]]: bold in HTML, plain in text.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace
from typing import Literal

# prospect: a company that is not a customer yet and wrote to us or booked a call (the team's mailbox).
Audience = Literal["member", "admin", "support", "prospect"]

# The mail's colours, light.
PRIMARY = "#1347b8"
INK = "#16181b"
TEXT = "#30343a"
MUTED = "#5e6167"
LINE = "#e8e4db"
PAGE = "#f4f2ec"
TINT = "#eef6f3"
FONT = "Arial,Helvetica,sans-serif"

_EMPHASIS = re.compile(r"\[\[(.+?)\]\]")


@dataclass(frozen=True)
class Message:
    """One e-mail in one language. Strings may hold {placeholders} and [[emphasis]]."""

    subject: str
    heading: str
    paragraphs: tuple[str, ...]
    preheader: str = ""
    eyebrow: str = ""
    # (label, address): the one thing to do. The address is a {placeholder} or a path on it.
    button: tuple[str, str] | None = None
    # (label, value) rows shown in a tinted box: when, where, which job, which invoice.
    facts: tuple[tuple[str, str], ...] = ()
    # Small print after the button: how long a link lasts, what to do if it was not you.
    note: str = ""
    greet: bool = True
    # The message this one answers, shown small and grey after the closing: (who wrote and when, its text).
    quote: tuple[str, str] | None = None


@dataclass(frozen=True)
class Brand:
    app: str
    logo_url: str
    site_url: str
    imprint_url: str
    privacy_url: str
    operator: str
    address: str
    support_email: str
    owner: str = ""
    audience: Audience = "member"
    language: str = "en"
    labels: dict[str, str] = field(default_factory=dict)


# The words around the content, per language.
CHROME: dict[str, dict[str, str]] = {
    "de": {
        "hello_named": "Guten Tag {name},", "hello": "Guten Tag,",
        "regards": "Viele Grüße", "team": "Ihr {app}-Team",
        "fallback": "Falls der Button nicht funktioniert, öffnen Sie diesen Link:",
        "imprint": "Impressum", "privacy": "Datenschutz", "owner": "Inhaber",
        "audience.member": "", "audience.admin": "Administration",
        "reason.member": "Sie erhalten diese E-Mail, weil sie zu Ihrem {app}-Konto gehört.",
        "reason.admin": "Sie erhalten diese E-Mail als Administrator von {app}.",
        "audience.support": "Support", "reason.support": "Sie erhalten diese E-Mail, weil Sie den {app}-Support kontaktiert haben.",
        "audience.prospect": "Für Partner", "reason.prospect": "Sie erhalten diese E-Mail, weil Sie {app} geschrieben oder einen Termin gebucht haben.",
        "questions": "Fragen? Schreiben Sie uns an",
    },
    "en": {
        "hello_named": "Hello {name},", "hello": "Hello,",
        "regards": "Best regards", "team": "The {app} team",
        "fallback": "If the button does not work, open this link:",
        "imprint": "Legal notice", "privacy": "Privacy", "owner": "Owner",
        "audience.member": "", "audience.admin": "Administration",
        "reason.member": "You receive this e-mail because it belongs to your {app} account.",
        "reason.admin": "You receive this e-mail as an administrator of {app}.",
        "audience.support": "Support", "reason.support": "You receive this e-mail because you contacted {app} support.",
        "audience.prospect": "For partners", "reason.prospect": "You receive this e-mail because you wrote to {app} or booked a call.",
        "questions": "Questions? Write to us at",
    },
}


def _fill(template: str, values: dict[str, str]) -> str:
    return template.format(**values)


def _plain(template: str, values: dict[str, str]) -> str:
    return _EMPHASIS.sub(r"\1", _fill(template, values))


def _rich(template: str, values: dict[str, str]) -> str:
    """The template is ours; every value is escaped before it goes in."""
    escaped = {key: html.escape(str(value), quote=True) for key, value in values.items()}
    text = _fill(html.escape(template, quote=False), escaped)
    return _EMPHASIS.sub(rf'<strong style="color:{INK};">\1</strong>', text).replace("\n", "<br>")


def safe_url(url: str) -> str:
    """Only web links go into a button or a link; anything else is dropped."""
    url = (url or "").strip()
    return url if re.match(r"^https?://[^\s\"'<>]+$", url) else ""


def render(message: Message, values: dict[str, str], brand: Brand) -> tuple[str, str, str]:
    """(subject, plain text, HTML) for one message."""
    chrome = CHROME.get(brand.language, CHROME["en"])
    values = {**values, "app": brand.app}
    if brand.language == "en" and message.paragraphs and message.greet:
        # "Hello Rita," then a capital; German goes on in lower case after "Guten Tag Rita,".
        first = message.paragraphs[0]
        message = replace(message, paragraphs=(first[:1].upper() + first[1:], *message.paragraphs[1:]))
    subject = _plain(message.subject, values)
    name = values.get("name", "")
    greeting = (chrome["hello_named"].format(name=name) if name else chrome["hello"]) if message.greet else ""
    raw = _fill(message.button[1], values).strip() if message.button else ""
    button_url = safe_url(raw)
    # Without a configured address a link is only a path (tests, a laptop): the text
    # still shows it, but nothing that is not a web link is ever clickable.
    text_url = button_url or (raw if re.match(r"^/[^\s]*$", raw) else "")
    team = chrome["team"].format(app=brand.app)
    reason = chrome[f"reason.{brand.audience}"].format(app=brand.app)
    # The operator only when it is not the app itself; an address only when one is configured.
    operator = brand.app + (f" · {brand.operator}" if brand.operator and brand.operator != brand.app else "") + (f" · {chrome['owner']}: {brand.owner}" if brand.owner else "")
    return subject, _text(message, values, chrome, greeting, text_url, team, operator, reason, brand), _html(message, values, chrome, greeting, button_url, team, operator, reason, brand)


def _text(message: Message, values: dict[str, str], chrome: dict[str, str], greeting: str, url: str, team: str, operator: str, reason: str, brand: Brand) -> str:
    parts: list[str] = []
    if greeting:
        parts.append(greeting)
    parts += [_plain(p, values) for p in message.paragraphs]
    if message.facts:
        parts.append("\n".join(f"{_plain(label, values)}: {_plain(value, values)}" for label, value in message.facts))
    if message.button and url:
        parts.append(f"{_plain(message.button[0], values)}:\n{url}")
    if message.note:
        parts.append(_plain(message.note, values))
    parts.append(f"{chrome['regards']}\n{team}")
    if message.quote:
        parts.append(f"{message.quote[0]}\n" + "\n".join(f"> {line}" for line in message.quote[1].splitlines()))
    parts.append("--\n" + "\n".join(x for x in (operator, brand.address, reason, f"{chrome['questions']} {brand.support_email}" if brand.support_email else "") if x))
    return "\n\n".join(parts) + "\n"


def _html(message: Message, values: dict[str, str], chrome: dict[str, str], greeting: str, url: str, team: str, operator: str, reason: str, brand: Brand) -> str:
    e = html.escape
    p = f"margin:0 0 16px;color:{TEXT};font-family:{FONT};font-size:15px;line-height:24px;"
    preheader = _plain(message.preheader or message.heading, values)
    audience = chrome[f"audience.{brand.audience}"]
    body: list[str] = []
    if greeting:
        body.append(f'<p style="{p}color:{INK};">{e(greeting)}</p>')
    body += [f'<p style="{p}">{_rich(par, values)}</p>' for par in message.paragraphs]
    if message.facts:
        rows = "".join(
            f'<tr><td valign="top" style="padding:4px 16px 4px 0;color:{MUTED};font-family:{FONT};font-size:13px;line-height:20px;white-space:nowrap;">{_rich(label, values)}</td>'
            f'<td valign="top" style="padding:4px 0;color:{INK};font-family:{FONT};font-size:14px;line-height:20px;font-weight:bold;overflow-wrap:anywhere;">{_rich(value, values)}</td></tr>'
            for label, value in message.facts
        )
        body.append(
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:4px 0 22px;"><tr>'
            f'<td bgcolor="{TINT}" style="padding:14px 18px;background-color:{TINT};border-left:3px solid {PRIMARY};">'
            f'<table role="presentation" cellspacing="0" cellpadding="0" border="0">{rows}</table></td></tr></table>'
        )
    if message.button and url:
        label = e(_plain(message.button[0], values))
        width = max(200, min(420, 24 + len(label) * 9))
        body.append(
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:6px 0 4px;"><tr>'
            f'<td align="center" bgcolor="{PRIMARY}" style="background-color:{PRIMARY};border-radius:6px;">'
            f'<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{e(url)}" style="height:48px;v-text-anchor:middle;width:{width}px;" arcsize="12%" stroke="f" fillcolor="{PRIMARY}">'
            f'<w:anchorlock/><center style="color:#ffffff;font-family:{FONT};font-size:15px;font-weight:bold;">{label}</center></v:roundrect><![endif]-->'
            f'<!--[if !mso]><!--><a href="{e(url)}" target="_blank" style="display:inline-block;background-color:{PRIMARY};border:1px solid {PRIMARY};border-radius:6px;padding:14px 26px;'
            f'color:#ffffff;font-family:{FONT};font-size:15px;line-height:20px;font-weight:bold;text-align:center;text-decoration:none;">{label}</a><!--<![endif]-->'
            '</td></tr></table>'
            f'<p style="margin:12px 0 0;color:{MUTED};font-family:{FONT};font-size:12px;line-height:19px;">{e(chrome["fallback"])}<br>'
            f'<a href="{e(url)}" target="_blank" style="color:{PRIMARY};text-decoration:underline;word-break:break-all;">{e(url)}</a></p>'
        )
    if message.note:
        body.append(f'<p style="margin:20px 0 0;color:{MUTED};font-family:{FONT};font-size:13px;line-height:21px;">{_rich(message.note, values)}</p>')
    eyebrow = (f'<p style="margin:0 0 10px;color:{PRIMARY};font-family:{FONT};font-size:11px;line-height:16px;font-weight:bold;letter-spacing:1.4px;text-transform:uppercase;">'
               f'{e(_plain(message.eyebrow, values))}</p>') if message.eyebrow else ""
    logo = (f'<img src="{e(brand.logo_url)}" width="150" height="29" alt="{e(brand.app)}" style="display:block;width:150px;height:29px;border:0;'
            f'color:{INK};font-family:{FONT};font-size:22px;font-weight:bold;line-height:29px;">') if safe_url(brand.logo_url) else f'<span style="color:{INK};font-family:{FONT};font-size:22px;font-weight:bold;">{e(brand.app)}</span>'
    quote = (f'<div style="margin:26px 0 0;padding:12px 0 0 14px;border-left:2px solid {LINE};color:{MUTED};font-family:{FONT};font-size:12px;line-height:19px;">'
             f'<p style="margin:0 0 6px;">{e(message.quote[0])}</p><p style="margin:0;">{e(message.quote[1]).replace(chr(10), "<br>")}</p></div>') if message.quote else ""
    site = safe_url(brand.site_url)
    links = " &nbsp;·&nbsp; ".join(
        f'<a href="{e(href)}" target="_blank" style="color:{MUTED};text-decoration:underline;">{e(text)}</a>'
        for href, text in ((site, site.split("://", 1)[-1].rstrip("/")), (safe_url(brand.imprint_url), chrome["imprint"]), (safe_url(brand.privacy_url), chrome["privacy"])) if href
    )
    return f"""<!doctype html>
<html lang="{e(brand.language)}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>{e(_plain(message.subject, values))}</title>
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
<style>
body,table,td,a{{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}}table,td{{mso-table-lspace:0pt;mso-table-rspace:0pt}}table{{border-collapse:collapse}}
img{{border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic}}a:focus-visible{{outline:3px solid {INK};outline-offset:3px}}
@media screen and (max-width:480px){{.outer{{padding:12px 8px!important}}.pad{{padding-left:22px!important;padding-right:22px!important}}.h1{{font-size:24px!important;line-height:31px!important}}}}
</style>
</head>
<body style="margin:0;padding:0;width:100%;background-color:{PAGE};color:{INK};font-family:{FONT};">
<div style="display:none;font-size:1px;line-height:1px;color:{PAGE};max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{e(preheader)}&#8199;&#65279;&#847;&#8199;&#65279;&#847;&#8199;&#65279;&#847;</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="{PAGE}"><tr><td class="outer" align="center" style="padding:32px 16px;">
<!--[if mso]><table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%;max-width:600px;background-color:#ffffff;border:1px solid {LINE};border-radius:8px;">
<tr><td height="4" bgcolor="{PRIMARY}" style="height:4px;line-height:4px;font-size:0;background-color:{PRIMARY};border-radius:8px 8px 0 0;">&nbsp;</td></tr>
<tr><td class="pad" style="padding:26px 40px 22px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>
<td align="left" valign="middle"><a href="{e(site or '#')}" target="_blank" style="text-decoration:none;">{logo}</a></td>
<td align="right" valign="middle" style="padding-left:12px;color:{MUTED};font-family:{FONT};font-size:12px;line-height:18px;">{e(audience)}</td>
</tr></table></td></tr>
<tr><td class="pad" style="padding:0 40px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td height="1" bgcolor="{LINE}" style="height:1px;font-size:0;line-height:1px;">&nbsp;</td></tr></table></td></tr>
<tr><td class="pad" style="padding:30px 40px 6px;">{eyebrow}<h1 class="h1" style="margin:0 0 22px;color:{INK};font-family:{FONT};font-size:27px;line-height:34px;font-weight:bold;letter-spacing:-0.4px;">{_rich(message.heading, values)}</h1>
{''.join(body)}
</td></tr>
<tr><td class="pad" style="padding:22px 40px 32px;color:{TEXT};font-family:{FONT};font-size:14px;line-height:22px;">
<p style="margin:0;">{e(chrome["regards"])}<br><strong style="color:{INK};">{e(team)}</strong></p>{quote}
</td></tr>
<tr><td class="pad" bgcolor="#f8f7f3" style="padding:22px 40px;background-color:#f8f7f3;border-top:1px solid {LINE};border-radius:0 0 8px 8px;color:{MUTED};font-family:{FONT};font-size:11px;line-height:18px;">
<p style="margin:0 0 8px;"><strong style="color:{TEXT};">{e(operator)}</strong>{"<br>" + e(brand.address) if brand.address else ""}</p>
<p style="margin:0 0 8px;">{links}</p>
<p style="margin:0;">{e(reason)} {e(chrome["questions"])} <a href="mailto:{e(brand.support_email)}" style="color:{MUTED};text-decoration:underline;">{e(brand.support_email)}</a></p>
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body>
</html>
"""
