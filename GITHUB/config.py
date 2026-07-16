"""Runtime configuration loaded from environment variables.
See .env.example for the full list of variables.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int = 0) -> int:
    """Return an integer environment variable, or a default if unset/invalid."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() else default


def _get_int_set(name: str) -> set[int]:
    """Parse a comma-separated list of IDs into a set of integers."""
    raw = os.getenv(name, "")
    return {int(part) for part in raw.split(",") if part.strip().isdigit()}


PREFIX: str = os.getenv("COMMAND_PREFIX", "$")

GUILD_ID: int = _get_int("GUILD_ID")
OWNER_ID: int = _get_int("OWNER_ID")
ADMIN_ROLE_ID: int = _get_int("ADMIN_ROLE_ID")
MOD_ROLE_ID: int = _get_int("MOD_ROLE_ID")

MANAGER_IDS: set[int] = _get_int_set("MANAGER_IDS")
ADMIN_IDS: set[int] = _get_int_set("ADMIN_IDS")
MOD_IDS: set[int] = _get_int_set("MOD_IDS")
