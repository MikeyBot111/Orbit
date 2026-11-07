"""
Shared Orbit configuration helpers.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_LOADED = False


def load_env() -> None:
    """Load .env from project root into process env (non-destructive)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    _ENV_LOADED = True


def get_env(name: str, default: str = "") -> str:
    load_env()
    return os.getenv(name, default)
