"""Load `.env` into the environment without overriding the shell (the shell wins).

Deliberately tiny instead of a dependency: `KEY=value` lines, `#` comments, optional quotes.
Values are never printed.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.removeprefix("export ").partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key and val and key not in os.environ:
            os.environ[key] = val
