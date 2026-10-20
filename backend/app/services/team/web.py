"""Reading a public web page for an agent (an organiser's official page, an embassy's rules).

Only http(s) on the default ports, only hosts whose every address is on the public internet
(checked again at each redirect), HTML or text only, with a size and time limit. The page
comes back as plain text with its links; it is data for the model, never instructions.
"""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 1_500_000
MAX_TEXT = 20_000
MAX_LINKS = 80
MAX_REDIRECTS = 4
TIMEOUT = 15.0
USER_AGENT = "ControlTower-Agent/1.0"


class Blocked(ValueError):
    pass


def check_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise Blocked("Only public http(s) addresses.")
    if parts.port not in (None, 80, 443):
        raise Blocked("Only the standard web ports.")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Blocked(f"The address {host} does not exist.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise Blocked("That address is not on the public internet.")
    return url.strip()


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "footer", "header", "address", "td"}

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base, self.skipping, self.title = base, 0, ""
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._anchor: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skipping += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href:
            href = urljoin(self.base, self._href)
            if href.startswith(("http://", "https://")) and len(self.links) < MAX_LINKS:
                self.links.append({"text": " ".join("".join(self._anchor).split())[:80], "url": href[:300]})
            self._href = None

    def handle_data(self, data: str) -> None:
        if self.skipping:
            return
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._anchor.append(data)
        self.parts.append(data)


def fetch(url: str) -> dict[str, Any]:
    current = check_url(url)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    current = check_url(urljoin(current, response.headers.get("location", "")))
                    continue
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind not in ("text/html", "text/plain", "application/xhtml+xml"):
                    return {"url": current, "status": response.status_code, "error": f"Not a web page ({kind or 'unknown type'})."}
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                text = body.decode(response.encoding or "utf-8", errors="replace")
                status = response.status_code
            break
        else:
            raise Blocked("Too many redirects.")
    if kind == "text/plain":
        return {"url": current, "status": status, "title": "", "text": text[:MAX_TEXT], "links": []}
    parser = _Text(current)
    parser.feed(text)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).split("\n")]
    body_text = "\n".join(line for line in lines if line)
    return {"url": current, "status": status, "title": " ".join(parser.title.split())[:200], "text": body_text[:MAX_TEXT], "links": parser.links}
