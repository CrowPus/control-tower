"""IMAP folder names are in "modified UTF-7" (RFC 3501 §5.1.3): "Entw&APw-rfe" is "Entwürfe"."""

from __future__ import annotations

import base64
import re


def decode(name: str) -> str:
    def part(match: re.Match[str]) -> str:
        chunk = match.group(1)
        if chunk == "":
            return "&"
        data = chunk.replace(",", "/")
        data += "=" * (-len(data) % 4)
        try:
            return base64.b64decode(data).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)

    return re.sub(r"&([A-Za-z0-9+,]*)-", part, name)


def encode(name: str) -> str:
    out: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            out.append("&" + base64.b64encode(buffer.encode("utf-16-be")).decode().rstrip("=").replace("/", ",") + "-")
            buffer = ""

    for char in name:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append("&-" if char == "&" else char)
        else:
            buffer += char
    flush()
    return "".join(out)
