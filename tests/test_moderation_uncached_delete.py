"""Tests for logging deletions of messages the bot no longer has cached.

discord.py only raises on_message_delete for messages still in its cache, which
holds the most recent messages and is emptied by a restart. Deleting anything
older used to leave no trace in the log. on_raw_message_delete now records
those too: where and when the message was posted, and who removed it when the
audit log says so. The content is gone by then, so the entry says that plainly.
"""

from __future__ import annotations

import datetime

import discord
import pytest
from discord.audit_logs import AuditLogEntry
from unittest.mock import AsyncMock, MagicMock

import app
from cogs import moderation
from utils import audit

GUILD = 1
CHANNEL = 500
LOG_CHANNEL = 600


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _skip_audit_delay(monkeypatch):
    """Never actually wait the audit-settle delay during tests."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(audit.asyncio, "sleep", _instant)


class User:
    """A member stub carrying just what a log line reads."""

    def __init__(self, user_id: int, name: str, *, bot: bool = False) -> None:
        self.id = user_id
        self.name = name
        self.bot = bot
        self.mention = f"<@{user_id}>"

    def __str__(self) -> str:
        return self.name


AUTHOR = User(100, "author")
MOD = User(900, "mod")


def make_entry(
    *,
    target: object,
    user: object = MOD,
    channel_id: int = CHANNEL,
    entry_id: int = 1,
    count: int = 1,
    age_seconds: float = 2.0,
) -> MagicMock:
    # spec makes a field real entries lack raise, as it would in production.
    entry = MagicMock(spec=AuditLogEntry)
    entry.target = target
    entry.user = user
    entry.extra = MagicMock()
    entry.extra.channel = discord.Object(id=channel_id)
    entry.id = entry_id
    entry.extra.count = count  # discord.py keeps it here, not on the entry
    entry.created_at = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(seconds=age_seconds)
    return entry


def make_guild(entries=(), raise_exc: Exception | None = None) -> MagicMock:
    """A guild whose audit_logs(...) yields `entries` (or raises)."""
    guild = MagicMock()
    guild.id = GUILD
    guild.name = "Test Guild"

    def _audit_logs(*, limit, action):
        async def _gen():
            if raise_exc is not None:
                raise raise_exc
            for entry in entries:
                yield entry

        return _gen()

    guild.audit_logs = _audit_logs
    return guild


def make_cog(guild: MagicMock, log_channel_id: int | None = LOG_CHANNEL):
    """The cog wired to `guild`, with its log channel resolved without a DB."""
    cog = moderation.Moderation(MagicMock())
    cog.bot.get_guild = MagicMock(return_value=guild)
    cog.bot.report_error = AsyncMock()
    if log_channel_id is None:
        log_channel = None
    else:
        log_channel = MagicMock()
        log_channel.id = log_channel_id
        log_channel.send = AsyncMock()
    cog._log_channel = AsyncMock(return_value=log_channel)
    return cog, log_channel


def make_payload(
    message_id: int = 1_300_000_000_000_000_000,
    *,
    channel_id: int = CHANNEL,
    guild_id: int | None = GUILD,
) -> discord.RawMessageDeleteEvent:
    data = {"id": str(message_id), "channel_id": str(channel_id)}
    if guild_id is not None:
        data["guild_id"] = str(guild_id)
    return discord.RawMessageDeleteEvent(data)


def logged_text(log_channel: MagicMock) -> str:
    log_channel.send.assert_awaited_once()
    return log_channel.send.await_args.kwargs["embed"].description


# --------------------------------------------------------------------------- #
# What gets logged
# --------------------------------------------------------------------------- #
async def test_a_moderator_deletion_names_the_moderator_and_the_author():
    cog, log_channel = make_cog(make_guild([make_entry(target=AUTHOR)]))

    await cog.on_raw_message_delete(make_payload())

    text = logged_text(log_channel)
    assert "**Author:** <@100> (author)" in text
    assert "**Deleted by:** <@900> (mod)" in text
    assert f"**Channel:** <#{CHANNEL}>" in text


async def test_with_no_audit_entry_the_author_removed_it():
    """Discord never logs a self-delete, so no entry means the author did it.
    Who the author was is unknown, since the message wasn't cached."""
    cog, log_channel = make_cog(make_guild([]))

    await cog.on_raw_message_delete(make_payload())

    text = logged_text(log_channel)
    assert "**Author:** unknown" in text
    assert "**Deleted by:** the author (no moderator deletion was recorded)" in text


async def test_an_unreadable_audit_log_is_reported_as_unknown():
    forbidden = discord.Forbidden.__new__(discord.Forbidden)  # no HTTP response
    cog, log_channel = make_cog(make_guild(raise_exc=forbidden))

    await cog.on_raw_message_delete(make_payload())

    text = logged_text(log_channel)
    assert "**Author:** unknown" in text
    assert "can't read this server's audit log" in text


async def test_the_entry_says_the_content_is_unavailable():
    cog, log_channel = make_cog(make_guild([]))

    await cog.on_raw_message_delete(make_payload())

    assert "**Content:** not available" in logged_text(log_channel)


async def test_the_post_time_is_read_from_the_message_id():
    posted = datetime.datetime(2026, 9, 30, 18, 0, tzinfo=datetime.timezone.utc)
    cog, log_channel = make_cog(make_guild([]))

    await cog.on_raw_message_delete(make_payload(discord.utils.time_snowflake(posted)))

    assert f"<t:{int(posted.timestamp())}:f>" in logged_text(log_channel)


async def test_an_author_no_longer_in_the_server_is_still_identified():
    """discord.py returns a bare Object for an audit target it can't resolve,
    which has an id but no name or mention."""
    guild = make_guild([make_entry(target=discord.Object(id=100))])
    cog, log_channel = make_cog(guild)

    await cog.on_raw_message_delete(make_payload())

    assert "**Author:** <@100>" in logged_text(log_channel)


async def test_a_failed_send_is_reported():
    cog, log_channel = make_cog(make_guild([]))
    log_channel.send.side_effect = discord.HTTPException.__new__(discord.HTTPException)

    await cog.on_raw_message_delete(make_payload())

    cog.bot.report_error.assert_awaited_once()


# --------------------------------------------------------------------------- #
# What is deliberately not logged
# --------------------------------------------------------------------------- #
async def test_a_cached_deletion_is_left_to_on_message_delete():
    """discord.py raises this event for every deletion. When the message was
    cached, on_message_delete logs it with its content, so logging it here too
    would double every entry."""
    cog, log_channel = make_cog(make_guild([make_entry(target=AUTHOR)]))
    payload = make_payload()
    payload.cached_message = MagicMock()

    await cog.on_raw_message_delete(payload)

    log_channel.send.assert_not_awaited()


async def test_a_deletion_outside_a_server_is_ignored():
    cog, log_channel = make_cog(make_guild([]))

    await cog.on_raw_message_delete(make_payload(guild_id=None))

    log_channel.send.assert_not_awaited()


async def test_a_message_removed_by_the_delete_command_is_skipped_once():
    """$delete logs one summary for the whole purge instead."""
    cog, log_channel = make_cog(make_guild([]))
    payload = make_payload()
    cog._purged_ids.add(payload.message_id)

    await cog.on_raw_message_delete(payload)

    log_channel.send.assert_not_awaited()
    assert payload.message_id not in cog._purged_ids


async def test_nothing_is_logged_without_a_log_channel():
    cog, _ = make_cog(make_guild([]), log_channel_id=None)

    await cog.on_raw_message_delete(make_payload())  # must not raise


async def test_a_deletion_in_the_log_channel_itself_is_not_logged():
    cog, log_channel = make_cog(make_guild([]))

    await cog.on_raw_message_delete(make_payload(channel_id=LOG_CHANNEL))

    log_channel.send.assert_not_awaited()


async def test_a_bot_message_is_not_logged():
    """Same rule as on_message_delete, which skips bot authors."""
    bot_author = User(200, "somebot", bot=True)
    cog, log_channel = make_cog(make_guild([make_entry(target=bot_author)]))

    await cog.on_raw_message_delete(make_payload())

    log_channel.send.assert_not_awaited()


# --------------------------------------------------------------------------- #
# The audit lookup without a known author
# --------------------------------------------------------------------------- #
async def test_uncached_lookup_matches_whoever_the_target_was():
    entry = make_entry(target=AUTHOR)

    assert await audit.find_uncached_deletion(make_guild([entry]), CHANNEL, {}) is entry


async def test_uncached_lookup_requires_the_same_channel():
    entry = make_entry(target=AUTHOR, channel_id=CHANNEL + 1)

    assert await audit.find_uncached_deletion(make_guild([entry]), CHANNEL, {}) is None


async def test_uncached_lookup_ignores_a_stale_entry():
    entry = make_entry(target=AUTHOR, age_seconds=3600)

    assert await audit.find_uncached_deletion(make_guild([entry]), CHANNEL, {}) is None


async def test_uncached_lookup_does_not_reuse_an_entry_already_attributed():
    entry = make_entry(target=AUTHOR, entry_id=7, count=1)
    cursor = {GUILD: (7, 1)}

    assert (
        await audit.find_uncached_deletion(make_guild([entry]), CHANNEL, cursor) is None
    )


async def test_an_entry_used_for_a_cached_deletion_is_not_claimed_again():
    """A moderator removes a recent message, which on_message_delete logs.
    Seconds later someone deletes an old message of their own in the same
    channel. The moderator's entry is still fresh, but it was already used, so
    the second deletion must not be pinned on the moderator."""
    entry = make_entry(target=AUTHOR, entry_id=7, count=1)
    guild = make_guild([entry])
    cog, log_channel = make_cog(guild)

    recent = MagicMock()
    recent.guild = guild
    recent.channel.id = CHANNEL
    recent.author = AUTHOR
    assert await cog._find_deleter(recent) is MOD

    await cog.on_raw_message_delete(make_payload())

    assert "**Deleted by:** the author" in logged_text(log_channel)


# --------------------------------------------------------------------------- #
# The message cache itself
# --------------------------------------------------------------------------- #
def test_the_bot_keeps_more_messages_than_the_discord_py_default():
    """A deletion can only be logged with its content while the message is
    cached. discord.py keeps 1,000 by default, shared across every channel."""
    bot = app.VibeBot()

    assert bot._connection.max_messages == app.MESSAGE_CACHE_SIZE
    assert app.MESSAGE_CACHE_SIZE > 1_000
