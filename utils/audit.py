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


class _Unknown:
    """Sentinel for 'the audit log could not be read'."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNKNOWN"


# Returned when the lookup itself failed. Distinct from None, which means the
# audit log was readable and simply had no entry — a genuine self-delete.
# Callers must not report a self-delete on UNKNOWN; they don't know.
UNKNOWN = _Unknown()


async def find_message_deleter(
    message: discord.Message,
    cursor: dict[int, tuple[int, int]],
    *,
    wait: bool = True,
) -> discord.abc.User | _Unknown | None:
    """Return the moderator who removed a message, or None for a self-delete.

    ``cursor`` is caller-owned state mapping guild id to the last audit entry
    seen as ``(entry_id, count)``. Discord reuses a single message_delete entry
    for repeated deletions by the same moderator, bumping its count rather than
    adding a new entry, so a match only counts as fresh when the id or the
    count has changed.

    Returns None when the author deleted their own message, and UNKNOWN when
    the audit log could not be read (most often a missing View Audit Log
    permission). Callers that punish on a self-delete must treat UNKNOWN as
    "can't tell", never as proof.
    """
    entry = await _find_delete_entry(
        message.guild, message.channel.id, message.author.id, cursor, wait=wait
    )
    if entry is None or entry is UNKNOWN:
        return entry
    return entry.user


async def find_uncached_deletion(
    guild: discord.Guild,
    channel_id: int,
    cursor: dict[int, tuple[int, int]],
    *,
    wait: bool = True,
) -> discord.AuditLogEntry | _Unknown | None:
    """Return the audit entry for a deletion whose message wasn't cached.

    The delete event for an uncached message carries only ids, so the author
    is unknown and the match can't be narrowed to them. When a moderator was
    responsible, the entry's ``user`` is the moderator and its ``target`` the
    author. None means no moderator deletion was recorded, so the author
    removed it; UNKNOWN means the audit log could not be read.

    ``cursor`` is the same state find_message_deleter uses, so an entry
    already matched to one deletion is never claimed by another.
    """
    return await _find_delete_entry(guild, channel_id, None, cursor, wait=wait)


async def _find_delete_entry(
    guild: discord.Guild,
    channel_id: int,
    author_id: int | None,
    cursor: dict[int, tuple[int, int]],
    *,
    wait: bool,
) -> discord.AuditLogEntry | _Unknown | None:
    """Return the fresh message_delete entry for a deletion, if there is one.

    ``author_id`` narrows the match to entries targeting that member. None
    accepts any target, for deletions whose author isn't known.
    """
    if wait:
        await asyncio.sleep(LOOKUP_DELAY_SECONDS)

    try:
        entries = [
            entry
            async for entry in guild.audit_logs(
                limit=LOOKUP_LIMIT,
                action=discord.AuditLogAction.message_delete,
            )
        ]
    except discord.Forbidden:
        log.info(
            "No View Audit Log permission in %s — cannot tell who deleted a "
            "message.",
            guild.name,
        )
        return UNKNOWN
    except discord.HTTPException:
        return UNKNOWN

    seen = cursor.get(guild.id)
    now = datetime.datetime.now(datetime.timezone.utc)
    for entry in entries:
        if entry.target is None:
            continue
        if author_id is not None and entry.target.id != author_id:
            continue
        # Compare channel ids, not objects: when the channel isn't in cache
        # discord.py hands back a bare Object, which never compares equal to a
        # TextChannel even for the same channel.
        entry_channel = getattr(entry.extra, "channel", None)
        if getattr(entry_channel, "id", None) != channel_id:
            continue
        if (now - entry.created_at).total_seconds() > MAX_AGE_SECONDS:
            continue
        count = _delete_count(entry)
        if seen is not None and entry.id == seen[0] and count <= seen[1]:
            continue
        cursor[guild.id] = (entry.id, count)
        return entry

    # Prime the cursor to the newest entry even on a self-delete, so the
    # baseline is set for the next lookup in this guild.
    if entries:
        newest = entries[0]
        cursor[guild.id] = (newest.id, _delete_count(newest))
    return None


def _delete_count(entry: discord.AuditLogEntry) -> int:
    """Return how many deletions an entry records.

    The count lives on ``entry.extra``, not on the entry. It is always present
    for message_delete entries; the default only guards a malformed payload.
    """
    return getattr(entry.extra, "count", 1)
