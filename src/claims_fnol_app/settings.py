"""Settings the composition root reads: the environment first, then `.env`.

`.env` is gitignored and holds the database URLs and the model key
(`.env.example` names them). Read here and nowhere else in the app.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def setting(name: str, default: str | None = None) -> str:
    if name in os.environ:
        return os.environ[name]
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip()
    if default is None:
        raise SystemExit(f"{name} is not set (environment or .env; see .env.example)")
    return default


__all__ = ["ROOT", "setting"]
