"""API-key authentication.

Clients send ``Authorization: Bearer <api-key>``. The server only stores SHA-256 hashes of
the keys (``BRIDGE_API_KEY_HASHES`` in .env), so the .env file alone does not reveal a
usable key. Keys are 256-bit random tokens, so an unsalted fast hash is sufficient (no
dictionary attack is possible); comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

KEY_PREFIX = "wcb_"
ENV_VAR = "BRIDGE_API_KEY_HASHES"
LABEL_RE = re.compile(r"^[\w.@-][\w .@()-]{0,59}$")
# One BRIDGE_API_KEY_HASHES line: optional quotes, optional inline comment. As in
# python-dotenv, "#" starts a comment after whitespace or right after "=" (empty value).
_LINE_START = re.compile(rf"^\s*(?:export\s+)?{ENV_VAR}\s*=")
_LINE = re.compile(
    rf"^(?P<head>\s*(?:export\s+)?{ENV_VAR}\s*=\s*)"
    r"(?P<q>[\"']?)(?P<value>.*?)(?P=q)(?P<comment>\s*(?<=[\s=])#.*)?\s*$"
)


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


def _hashes_in(env_file: Path) -> list[str]:
    """The hashes the service will see (python-dotenv, as used by pydantic-settings)."""
    raw = dotenv_values(env_file).get(ENV_VAR) or ""
    return [h.strip() for h in raw.split(",") if h.strip()]


def add_hash_to_env_file(env_file: Path, key_hash: str, label: str) -> list[str]:
    """Append ``key_hash`` to BRIDGE_API_KEY_HASHES in ``env_file``; return all hashes.

    The label and date go into the line's inline comment, so a key can be identified
    (and revoked) later. Atomic write, file mode kept. The result is re-read with the
    service's parser and must equal the old hashes plus the new one, otherwise the
    original file is restored and ``ValueError`` raised.
    """
    if not LABEL_RE.match(label):
        raise ValueError(f"invalid key name {label!r} (letters, digits, space, . @ - ( ) _)")
    env_file = env_file.resolve()
    original = env_file.read_text(encoding="utf-8")
    before = _hashes_in(env_file)
    note = f"{label} ({datetime.now(UTC).date().isoformat()})"
    lines = original.splitlines()
    matches = [i for i, line in enumerate(lines) if _LINE_START.match(line)]
    if not matches:
        lines.append(f"{ENV_VAR}={key_hash}   # {note}")
    else:
        i = matches[-1]  # python-dotenv: the last definition wins
        m = _LINE.match(lines[i])
        if m is None:
            raise ValueError(f"cannot parse the {ENV_VAR} line in {env_file}; edit it by hand")
        head, value = m["head"], m["value"].strip().rstrip(",")
        comment = (m["comment"] or "").rstrip()
        if value:
            value = f"{value},{key_hash}"
            comment = f"{comment}, {note}" if comment else f"   # {note}"
        else:  # first key: drop the template's padding and explanatory comment
            head, value, comment = head.rstrip(), key_hash, f"   # {note}"
        lines[i] = f"{head}{m['q']}{value}{m['q']}{comment}"
    _atomic_write(env_file, "\n".join(lines) + "\n")
    after = _hashes_in(env_file)
    if after != [*before, key_hash]:
        _atomic_write(env_file, original)
        raise ValueError(f"{env_file} could not be updated safely (restored); edit it by hand")
    return after


def _atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
