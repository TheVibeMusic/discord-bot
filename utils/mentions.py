"""Helpers for controlling who a message is allowed to ping.

``discord.AllowedMentions(users=[...])`` leaves ``everyone`` and ``roles`` at
their permissive defaults, which is easy to miss and has bitten this bot more
than once. The bot also sets a safe client-wide default in app.py; these
helpers are for the call sites that need to be narrower still, in particular
anywhere member-supplied text reaches a message.
"""

from __future__ import annotations

import discord


def only_ping(*user_ids: int) -> discord.AllowedMentions:
    """Allow pinging exactly these members and nothing else.

    Called with no arguments it permits no pings at all, which is what you
    want around text a member typed.
    """
    return discord.AllowedMentions(
        everyone=False,
        roles=False,
        users=[discord.Object(id=user_id) for user_id in user_ids],
    )


def only_ping_role(role: discord.Role) -> discord.AllowedMentions:
    """Allow pinging exactly this role and nothing else."""
    return discord.AllowedMentions(everyone=False, roles=[role], users=False)
