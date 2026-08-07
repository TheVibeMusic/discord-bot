"""Working out who deleted a message.

A message-delete gateway event carries no actor, so the guild audit log is the
only source. Discord writes no audit entry at all when a member deletes their
own message, which makes "no matching entry" the signal for a self-delete.

Both the deletion log and the counting game need this, from opposite
directions: one wants to name the moderator, the other wants to know whether
the author removed their own count.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

import discord

log = logging.getLogger("vibe.audit")

# Discord writes the audit entry a moment after the delete event arrives, so
# the lookup waits briefly before reading it.
LOOKUP_DELAY_SECONDS = 1.5
LOOKUP_LIMIT = 5

# A genuine moderator deletion is logged within a couple of seconds of the
# event. Anything older is stale (for example an entry left over from before a
# restart) and must not be matched to a fresh self-delete.
MAX_AGE_SECONDS = 15


async def find_message_deleter(
    message: discord.Message,
    cursor: dict[int, tuple[int, int]],
    *,
    wait: bool = True,
) -> discord.abc.User | None:
    """Return the moderator who removed a message, or None for a self-delete.

    ``cursor`` is caller-owned state mapping guild id to the last audit entry
    seen as ``(entry_id, count)``. Discord reuses a single message_delete entry
    for repeated deletions by the same moderator, bumping its count rather than
    adding a new entry, so a match only counts as fresh when the id or the
    count has changed.

    Returns None when the author deleted their own message, when the bot lacks
    the View Audit Log permission, or when the lookup fails. Callers that
    punish on a self-delete should treat a permission failure as "unknown"
    rather than proof.
    """
    if wait:
        await asyncio.sleep(LOOKUP_DELAY_SECONDS)

    try:
        entries = [
            entry
            async for entry in message.guild.audit_logs(
                limit=LOOKUP_LIMIT,
                action=discord.AuditLogAction.message_delete,
            )
        ]
    except discord.Forbidden:
        log.info(
            "No View Audit Log permission in %s — cannot tell who deleted a "
            "message.",
            message.guild.name,
        )
        return None
    except discord.HTTPException:
        return None

    seen = cursor.get(message.guild.id)
    now = datetime.datetime.now(datetime.timezone.utc)
    for entry in entries:
        if entry.target is None or entry.target.id != message.author.id:
            continue
        if getattr(entry.extra, "channel", None) != message.channel:
            continue
        if (now - entry.created_at).total_seconds() > MAX_AGE_SECONDS:
            continue
        if seen is not None and entry.id == seen[0] and entry.count <= seen[1]:
            continue
        cursor[message.guild.id] = (entry.id, entry.count)
        return entry.user

    # Prime the cursor to the newest entry even on a self-delete, so the
    # baseline is set for the next lookup in this guild.
    if entries:
        newest = entries[0]
        cursor[message.guild.id] = (newest.id, newest.count)
    return None


async def audit_log_readable(guild: discord.Guild) -> bool:
    """Whether the bot can read this guild's audit log.

    Used before punishing someone for a self-delete: without the permission
    every deletion looks like a self-delete, so the feature must stay quiet
    rather than blame people at random.
    """
    try:
        async for _ in guild.audit_logs(limit=1):
            break
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False
    return True
