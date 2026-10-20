"""Reading a mail as the server gives it (RFC 5322 / MIME), and making HTML safe to show and to send.

An e-mail's HTML is someone else's content: it is cleaned before it is stored (no scripts, forms, event
handlers or frames), and the admin app shows it in a sandboxed frame whose content policy blocks remote
pictures until the reader asks for them (they are how senders see who opened a mail).
"""

from __future__ import annotations

import email
import email.errors
import email.policy
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html import escape, unescape
from typing import Any

import nh3

MAX_TEXT = 300_000
MAX_HTML = 1_500_000

# --- HTML ----------------------------------------------------------------------------------------------
_SHOW_TAGS = {
    "a", "abbr", "address", "b", "big", "blockquote", "br", "caption", "center", "cite", "code", "col", "colgroup", "dd", "del", "div",
    "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre", "q",
    "s", "small", "span", "strike", "strong", "style", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul",
}
_SHOW_ATTRS = {
    "*": {"style", "class", "align", "valign", "width", "height", "bgcolor", "color", "dir", "title", "border", "cellpadding", "cellspacing",
          "colspan", "rowspan", "face", "size", "lang", "id"},
    "a": {"href", "name"},
    "img": {"src", "alt"},
}
_WRITE_TAGS = {"a", "b", "blockquote", "br", "div", "em", "font", "h1", "h2", "h3", "hr", "i", "img", "li", "ol", "p", "pre", "s", "small",
               "span", "strike", "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "u", "ul", "code"}
_WRITE_ATTRS = {"*": {"style", "align", "width", "height", "color", "face", "size", "colspan", "rowspan", "border", "cellpadding", "cellspacing"},
                "a": {"href"}, "img": {"src", "alt"}}
_IMAGE_DATA = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,", re.IGNORECASE)


def _filter(tag: str, attr: str, value: str) -> str | None:
    lowered = value.strip().lower()
    if attr == "href" and lowered.startswith(("data:", "cid:")):
        return None
    if tag == "img" and attr == "src" and lowered.startswith("data:") and not _IMAGE_DATA.match(lowered):
        return None
    if attr == "style" and ("expression(" in lowered or "javascript:" in lowered or "behavior:" in lowered):
        return None
    return value


def clean_for_display(html: str) -> str:
    """Someone else's HTML, safe to show in the sandboxed frame."""
    if not html:
        return ""
    return nh3.clean(html[: MAX_HTML * 2], tags=_SHOW_TAGS, clean_content_tags={"script"}, attributes=_SHOW_ATTRS,
                     url_schemes={"http", "https", "mailto", "tel", "cid", "data"}, attribute_filter=_filter,
                     set_tag_attribute_values={"a": {"target": "_blank"}}, link_rel="noopener noreferrer nofollow", strip_comments=True)[:MAX_HTML]


def clean_for_sending(html: str) -> str:
    """What the team wrote in the editor (and their signature), as it may go out."""
    if not html:
        return ""
    return nh3.clean(html[:MAX_HTML], tags=_WRITE_TAGS, clean_content_tags={"script", "style"}, attributes=_WRITE_ATTRS,
                     url_schemes={"http", "https", "mailto", "tel", "cid", "data"}, attribute_filter=_filter, link_rel=None, strip_comments=True)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"(?is)<(head|style|script|title)\b.*?</\1\s*>", " ", html)
    text = re.sub(r"(?i)<\s*a\b[^>]*href=\"(https?://[^\"]+)\"[^>]*>(.*?)</a\s*>",
                  lambda m: m.group(2) if unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip() in (m.group(1), "") else f"{m.group(2)} ({m.group(1)})", text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr|/table|/blockquote)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "• ", text)
    text = re.sub(r"(?i)<\s*/t[dh]\s*>", "\t", text)
    text = unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+\n", "\n", re.sub(r"[ \t\f\v]{2,}", " ", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def text_to_html(text: str) -> str:
    """Plain text as HTML (links clickable, lines kept)."""
    escaped = escape(text or "")
    linked = re.sub(r"(https?://[^\s<>&\"]+)", r'<a href="\1">\1</a>', escaped)
    return '<div style="white-space:pre-wrap">' + linked + "</div>"


# --- headers ---------------------------------------------------------------------------------------------
def decode_value(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    try:
        return str(make_header(decode_header(raw)))
    except (UnicodeDecodeError, LookupError, ValueError, email.errors.HeaderParseError):
        return raw


def addresses(values: list[Any]) -> list[list[str]]:
    out: list[list[str]] = []
    for name, address in getaddresses([decode_value(v) for v in values if v]):
        address = address.strip()
        if "@" in address:
            out.append([name.strip().strip('"')[:200], address.lower()[:320]])
    return out[:200]


def _ids(value: str) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


def normalise_subject(subject: str) -> str:
    """The subject without reply and forward prefixes, for prefixes and search."""
    return re.sub(r"^\s*((re|aw|wg|fw|fwd|antw|sv|vs)(\[\d+\])?\s*:\s*)+", "", subject or "", flags=re.IGNORECASE).strip()


# --- the body -------------------------------------------------------------------------------------------
@dataclass
class Parsed:
    message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    subject: str = ""
    from_name: str = ""
    from_address: str = ""
    to: list[list[str]] = field(default_factory=list)
    cc: list[list[str]] = field(default_factory=list)
    bcc: list[list[str]] = field(default_factory=list)
    reply_to: list[list[str]] = field(default_factory=list)
    sent_at: datetime | None = None
    text: str = ""
    html: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    preview: str = ""
    list_unsubscribe: str = ""
    draft_ref: int | None = None


def _payload_text(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
    except (AssertionError, ValueError):
        return ""
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _filename(part: Message) -> str:
    name = part.get_filename()
    if not name and part.get_content_type() == "message/rfc822":
        name = "message.eml"
    return decode_value(name).replace("/", "_").replace("\\", "_").strip()[:200] if name else ""


def _is_attachment(part: Message) -> bool:
    disposition = (part.get("Content-Disposition") or "").split(";")[0].strip().lower()
    if disposition == "attachment":
        return True
    if part.get_content_maintype() == "multipart":
        return False
    if part.get_content_type() in ("text/plain", "text/html") and not part.get_filename():
        return False
    return True


def walk(message: Message) -> list[Message]:
    """Every part in a fixed order; a part's index is its place in this list (to fetch it again later)."""
    return list(message.walk())


def _preview(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith(">")]
    return re.sub(r"\s+", " ", " ".join(lines))[:200]


def parse(raw: bytes) -> Parsed:
    message = email.message_from_bytes(raw, policy=email.policy.compat32)
    out = Parsed()
    out.message_id = (_ids(message.get("Message-ID") or "") or [""])[0][:300]
    out.in_reply_to = (_ids(message.get("In-Reply-To") or "") or [""])[0][:300]
    out.references = " ".join(_ids(message.get("References") or ""))[-5000:]
    out.subject = decode_value(message.get("Subject")).replace("\r", "").replace("\n", " ")[:1000]
    sender = addresses([message.get("From")]) or addresses([message.get("Sender")])
    if sender:
        out.from_name, out.from_address = sender[0]
    else:
        name, address = parseaddr(decode_value(message.get("From")))
        out.from_name, out.from_address = name[:200], address[:320]
    out.to = addresses(message.get_all("To") or [])
    out.cc = addresses(message.get_all("Cc") or [])
    out.bcc = addresses(message.get_all("Bcc") or [])
    out.reply_to = addresses(message.get_all("Reply-To") or [])
    try:
        when = parsedate_to_datetime(message.get("Date") or "")
        out.sent_at = (when if when.tzinfo else when.replace(tzinfo=UTC)).astimezone(UTC)
    except (TypeError, ValueError, IndexError):
        out.sent_at = None
    out.list_unsubscribe = decode_value(message.get("List-Unsubscribe"))[:1000]
    ref = (message.get("X-Tower-Draft") or "").strip()
    out.draft_ref = int(ref) if ref.isdigit() else None
    plain, html = "", ""
    for index, part in enumerate(walk(message)):
        if part.get_content_maintype() == "multipart":
            continue
        if _is_attachment(part):
            cid = (part.get("Content-ID") or "").strip().strip("<>")[:100]
            disposition = (part.get("Content-Disposition") or "").split(";")[0].strip().lower()
            try:
                payload = part.get_payload(decode=True)
            except (AssertionError, ValueError):
                payload = None
            size = len(payload) if isinstance(payload, bytes) else 0
            out.attachments.append({"index": index, "filename": _filename(part) or f"part-{index}", "mime": part.get_content_type()[:120], "size": size, "cid": cid,
                                    "inline": bool(cid) and disposition != "attachment" and part.get_content_maintype() == "image"})
            continue
        if part.get_content_type() == "text/plain" and not plain:
            plain = _payload_text(part)
        elif part.get_content_type() == "text/html" and not html:
            html = _payload_text(part)
    out.html = clean_for_display(html) if html else ""
    out.text = (plain.replace("\r\n", "\n") if plain else html_to_text(html))[:MAX_TEXT]
    out.preview = _preview(out.text)
    return out


def part_of(raw: bytes, index: int) -> tuple[str, str, bytes] | None:
    """(file name, type, content) of one part by its index."""
    message = email.message_from_bytes(raw, policy=email.policy.compat32)
    parts = walk(message)
    if not 0 <= index < len(parts):
        return None
    part = parts[index]
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload()
        data = inner[0].as_bytes() if isinstance(inner, list) and inner and isinstance(inner[0], Message) else b""
    else:
        try:
            payload = part.get_payload(decode=True)
        except (AssertionError, ValueError):
            payload = None
        data = payload if isinstance(payload, bytes) else b""
    return _filename(part) or f"part-{index}", part.get_content_type(), data
