"""Runtime configuration loaded from environment variables.

All server-specific values (IDs, roles, access lists) live in the environment
so nothing is hardcoded. See .env.example for the full list of variables.
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

# Channel that receives bot error reports (overridable via env).
ERROR_CHANNEL_ID: int = _get_int("ERROR_CHANNEL_ID", 1523742065450815528)

# Channels where anyone may use slash commands. Threads under these channels
# count too. Leaving this unset disables the restriction entirely, so a missing
# value can never lock the whole server out of the bot.
COMMAND_CHANNEL_IDS: set[int] = _get_int_set("COMMAND_CHANNEL_IDS")

# Users who may use slash commands anywhere, for testing and debugging. The
# server owner is always included so a bad config can't lock them out.
COMMAND_EXEMPT_IDS: set[int] = _get_int_set("COMMAND_EXEMPT_IDS") | (
    {OWNER_ID} if OWNER_ID else set()
)

# Staff and developers who are never blocked from the counting game for
# deleting their own counts — they delete messages as part of testing and
# moderating. The server owner is always included.
COUNTING_EXEMPT_IDS: set[int] = _get_int_set("COUNTING_EXEMPT_IDS") | (
    {OWNER_ID} if OWNER_ID else set()
)
