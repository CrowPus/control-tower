"""Checks on files people attach (agent chat, outgoing mail): executable content is refused, and every file goes
through ClamAV when CLAMD_HOST is set (the clamd INSTREAM protocol)."""

from __future__ import annotations

import socket
import struct
from typing import Literal

from ..config import settings

# File starts that mean "a program", whatever the file's name says.
EXECUTABLE_SIGNATURES: list[tuple[bytes, str]] = [
    (b"MZ", "Executable.WindowsPE"),
    (b"\x7fELF", "Executable.LinuxELF"),
    (b"\xfe\xed\xfa\xce", "Executable.MachO32"),
    (b"\xfe\xed\xfa\xcf", "Executable.MachO64"),
    (b"\xce\xfa\xed\xfe", "Executable.MachO32_LE"),
    (b"\xcf\xfa\xed\xfe", "Executable.MachO64_LE"),
    (b"#!/", "Script.Shebang"),
]

# clamd reads the stream in chunks; each is sent with its length in front, a zero length ends it.
CHUNK = 65536
REPLY_LIMIT = 4096

Verdict = Literal["clean", "infected", "unavailable", "off"]


def antivirus(data: bytes) -> Verdict:
    """ClamAV's verdict: clean, infected, unavailable (set up but not reached), or off (no scanner set up)."""
    cfg = settings()
    if not cfg.clamd_host:
        return "off"
    try:
        with socket.create_connection((cfg.clamd_host, cfg.clamd_port), timeout=10) as conn:
            conn.sendall(b"zINSTREAM\0")
            for start in range(0, len(data), CHUNK):
                part = data[start:start + CHUNK]
                conn.sendall(struct.pack("!I", len(part)) + part)
            conn.sendall(struct.pack("!I", 0))
            reply = b""
            while not reply.endswith(b"\0") and len(reply) < REPLY_LIMIT:
                got = conn.recv(1024)
                if not got:
                    break
                reply += got
    except OSError:
        return "unavailable"
    return "clean" if reply == b"stream: OK\0" else "infected"
