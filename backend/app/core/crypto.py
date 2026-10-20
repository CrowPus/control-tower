"""Authenticated encryption for stored integration credentials (spec §49).

AES-256-GCM with a key derived from `ENCRYPTION_SECRET` via HKDF. The key
material lives in the environment (or a secret manager that populates it) and
never beside the ciphertext in the database. Each value gets a fresh 96-bit
nonce; the ciphertext is versioned so the scheme can rotate later.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import settings
from .errors import AppError

_VERSION = b"v1"
_INFO = b"opportunity-agent/integration-credentials"


class CryptoNotConfigured(AppError):
    status_code = 409
    code = "ENCRYPTION_NOT_CONFIGURED"
    requires_user_action = True


def _key() -> bytes:
    secret = settings().encryption_secret
    if not secret or len(secret) < 16:
        raise CryptoNotConfigured(
            "ENCRYPTION_SECRET (at least 16 characters) must be set before credentials "
            "can be stored."
        )
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO)
    return hkdf.derive(secret.encode("utf-8"))


def encrypt(plaintext: str, *, associated_data: str = "") -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), associated_data.encode("utf-8"))
    return (_VERSION + b"." + base64.urlsafe_b64encode(nonce + ct)).decode("ascii")


def decrypt(token: str, *, associated_data: str = "") -> str:
    try:
        version, payload = token.encode("ascii").split(b".", 1)
        if version != _VERSION:
            raise ValueError("unsupported ciphertext version")
        raw = base64.urlsafe_b64decode(payload)
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(_key()).decrypt(nonce, ct, associated_data.encode("utf-8")).decode("utf-8")
    except (ValueError, InvalidTag) as exc:
        raise AppError(
            "Stored credential could not be decrypted (key changed or data corrupted).",
            code="CREDENTIAL_UNREADABLE",
            status_code=409,
            requires_user_action=True,
        ) from exc


def is_configured() -> bool:
    secret = settings().encryption_secret
    return bool(secret) and len(secret) >= 16
