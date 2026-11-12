"""API-key authentication.

Clients send ``Authorization: Bearer <api-key>``. The server only stores SHA-256 hashes of
the keys (``BRIDGE_API_KEY_HASHES`` in .env), so the .env file alone does not reveal a
usable key. Keys are 256-bit random tokens, so an unsalted fast hash is sufficient (no
dictionary attack is possible); comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

KEY_PREFIX = "wcb_"


def generate_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def verify_api_key(key: str, allowed_hashes: list[str]) -> bool:
    if not key or not allowed_hashes:
        return False
    candidate = hash_api_key(key)
    # Evaluate every hash (no early exit) to keep timing independent of the match position.
    matches = [hmac.compare_digest(candidate, h) for h in allowed_hashes]
    return any(matches)
