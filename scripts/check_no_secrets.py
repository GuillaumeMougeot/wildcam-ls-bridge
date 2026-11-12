"""Fail if the VALUE of any secret in .env appears in the given files.

Used by pre-commit and `make secrets`. Catches the classic mistake of pasting the ERDA
sharelink or the Label Studio token into code, docs or the journal (the legacy rsync.py
had the sharelink hard-coded).

    python scripts/check_no_secrets.py FILE [FILE ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SECRET_KEYS = re.compile(r"(SHARELINK|TOKEN|PASSWORD|SECRET|API_KEY)", re.IGNORECASE)
MIN_LEN = 6


def load_secrets(env_file: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    if not env_file.is_file():
        return secrets
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip("'\"")
        if SECRET_KEYS.search(key) and len(value) >= MIN_LEN:
            secrets[key.strip()] = value
    return secrets


def main(argv: list[str]) -> int:
    secrets = load_secrets(Path(".env"))
    found = 0
    for name in argv:
        path = Path(name)
        if not path.is_file() or path.name == ".env":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for key, value in secrets.items():
            if value in text:
                print(f"{path}: contains the value of {key} from .env", file=sys.stderr)
                found += 1
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
