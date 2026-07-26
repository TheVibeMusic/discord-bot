"""Tests for Moderation._find_deleter — attributing a message deletion.

A message-delete gateway event carries no actor, so the cog reads the guild
audit log to name who removed a message. Discord never logs a self-delete, so
"no matching entry" means the author removed their own message. These tests
cover the tricky cases: a stale entry left over across a restart, Discord
reusing one entry for rapid same-moderator deletions, and a missing View Audit
Log permission.
"""

from __future__ import annotations

import datetime

import discord
import pytest
from unittest.mock import MagicMock

from cogs import moderation


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _skip_audit_delay(monkeypatch):
    """Never actually wait the 1.5s audit-settle delay during tests."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(moderation.asyncio, "sleep", _instant)


def make_cog() -> moderation.Moderation:
    return moderation.Moderation(MagicMock())


def make_message(author_id: int, channel: object, guild_id: int = 1) -> MagicMock:
    message = MagicMock()
    message.author.id = author_id
    message.channel = channel
    message.guild.id = guild_id
    message.guild.name = "Test Guild"
    return message


def make_entry(
    *,
    target_id: int,
    channel: object,
    user: object,
    entry_id: int,
    count: int = 1,
    age_seconds: float = 2.0,
) -> MagicMock:
    entry = MagicMock()
    entry.target = MagicMock()
    entry.target.id = target_id
    entry.extra = MagicMock()
    entry.extra.channel = channel
    entry.user = user
    entry.id = entry_id
    entry.count = count
    entry.created_at = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(seconds=age_seconds)
    return entry


def wire_audit_log(message: MagicMock, entries, raise_exc: Exception | None = None):
    """Make message.guild.audit_logs(...) yield `entries` (or raise)."""

    def _audit_logs(*, limit, action):
        async def _gen():
            if raise_exc is not None:
                raise raise_exc
            for entry in entries:
                yield entry

        return _gen()

    message.guild.audit_logs = _audit_logs


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_fresh_mod_delete_is_attributed_to_the_moderator():
    cog = make_cog()
    channel = object()
    mod = object()
    message = make_message(author_id=100, channel=channel)
    wire_audit_log(
        message,
        [make_entry(target_id=100, channel=channel, user=mod, entry_id=1)],
    )

    assert await cog._find_deleter(message) is mod


async def test_self_delete_with_no_audit_entry_returns_none():
    cog = make_cog()
    channel = object()
    message = make_message(author_id=100, channel=channel)
    wire_audit_log(message, [])  # self-deletes are never logged

    assert await cog._find_deleter(message) is None


async def test_stale_entry_after_restart_is_not_blamed_on_the_mod():
    """Tyro's edge case: empty cursor (post-restart) + a self-delete whose
    author had an OLDER mod-deletion in the same channel. The stale entry must
    not be attributed to that moderator."""
    cog = make_cog()  # fresh cog => empty audit cursor, as after a restart
    channel = object()
    mod = object()
    message = make_message(author_id=100, channel=channel)
    wire_audit_log(
        message,
        [
            make_entry(
                target_id=100,
                channel=channel,
                user=mod,
                entry_id=42,
                age_seconds=3600,  # an hour old — from before the restart
            )
        ],
    )

    assert await cog._find_deleter(message) is None


async def test_self_delete_right_after_a_fresh_mod_delete_returns_none():
    """A mod deletes the author's message (attributed correctly). Moments later
    the author self-deletes another message in the same channel. The unchanged
    audit entry must not be re-attributed to the mod."""
    cog = make_cog()
    channel = object()
    mod = object()

    first = make_message(author_id=100, channel=channel)
    wire_audit_log(
        first,
        [make_entry(target_id=100, channel=channel, user=mod, entry_id=7, count=1)],
    )
    assert await cog._find_deleter(first) is mod  # cursor now (7, 1)

    second = make_message(author_id=100, channel=channel)
    wire_audit_log(
        second,
        [make_entry(target_id=100, channel=channel, user=mod, entry_id=7, count=1)],
    )
    assert await cog._find_deleter(second) is None


async def test_repeated_mod_deletes_bump_count_and_stay_attributed():
    """Discord reuses one entry for rapid same-mod deletions, bumping its
    count. Each bump is a real new deletion and should attribute to the mod."""
    cog = make_cog()
    channel = object()
    mod = object()

    first = make_message(author_id=100, channel=channel)
    wire_audit_log(
        first,
        [make_entry(target_id=100, channel=channel, user=mod, entry_id=7, count=1)],
    )
    assert await cog._find_deleter(first) is mod  # cursor (7, 1)

    second = make_message(author_id=100, channel=channel)
    wire_audit_log(
        second,
        [make_entry(target_id=100, channel=channel, user=mod, entry_id=7, count=2)],
    )
    assert await cog._find_deleter(second) is mod  # count bumped => fresh


async def test_missing_view_audit_log_permission_returns_none():
    cog = make_cog()
    channel = object()
    message = make_message(author_id=100, channel=channel)
    forbidden = discord.Forbidden.__new__(discord.Forbidden)  # no real HTTP response
    wire_audit_log(message, [], raise_exc=forbidden)

    assert await cog._find_deleter(message) is None


async def test_entry_for_a_different_user_is_ignored():
    cog = make_cog()
    channel = object()
    mod = object()
    message = make_message(author_id=100, channel=channel)
    wire_audit_log(
        message,
        [make_entry(target_id=999, channel=channel, user=mod, entry_id=1)],
    )

    assert await cog._find_deleter(message) is None


async def test_entry_for_a_different_channel_is_ignored():
    cog = make_cog()
    channel = object()
    other_channel = object()
    mod = object()
    message = make_message(author_id=100, channel=channel)
    wire_audit_log(
        message,
        [make_entry(target_id=100, channel=other_channel, user=mod, entry_id=1)],
    )

    assert await cog._find_deleter(message) is None
