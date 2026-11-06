"""Tests for utils.audit against real discord.py audit log entries.

The other deletion tests use hand-built stand-ins for audit entries, and those
stand-ins put the repeat count on the entry itself. discord.py keeps it on
entry.extra instead, so every real lookup raised AttributeError: once the bot
could read the audit log, no deletion was logged at all. These tests build the
entries the way discord.py does, from the API payload, so the helper is
checked against the shape it actually receives.
"""

from __future__ import annotations

import datetime

import discord
import pytest
from discord.audit_logs import AuditLogEntry
from unittest.mock import AsyncMock, MagicMock

from cogs import moderation
from utils import audit

GUILD_ID = 1000
CHANNEL_ID = 500
LOG_CHANNEL_ID = 600
AUTHOR_ID = 100
MOD_ID = 900


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _skip_audit_delay(monkeypatch):
    """Never actually wait the audit-settle delay during tests."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(audit.asyncio, "sleep", _instant)


@pytest.fixture
def guild() -> discord.Guild:
    """A real Guild object, offline, with no channels or members cached."""
    client = discord.Client(intents=discord.Intents.none())
    return discord.Guild(
        data={"id": str(GUILD_ID), "name": "Test Guild", "channels": []},
        state=client._connection,
    )


@pytest.fixture
def serve(monkeypatch):
    """Make Guild.audit_logs yield the given entries, newest first."""

    def _serve(entries: list[AuditLogEntry]) -> None:
        def _audit_logs(self, *, limit, action):
            async def _gen():
                for entry in entries:
                    yield entry

            return _gen()

        monkeypatch.setattr(discord.Guild, "audit_logs", _audit_logs)

    return _serve


def make_user(guild: discord.Guild, user_id: int, name: str) -> discord.User:
    return discord.User(
        state=guild._state,
        data={
            "id": str(user_id),
            "username": name,
            "discriminator": "0",
            "global_name": None,
            "avatar": None,
        },
    )


def delete_entry(
    guild: discord.Guild,
    *,
    entry_id: int | None = None,
    target_id: int = AUTHOR_ID,
    channel_id: int = CHANNEL_ID,
    count: int = 1,
    age_seconds: float = 2.0,
) -> AuditLogEntry:
    """A message_delete entry built from the payload Discord sends."""
    created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=age_seconds
    )
    data = {
        "id": str(entry_id or discord.utils.time_snowflake(created)),
        "action_type": discord.AuditLogAction.message_delete.value,
        "user_id": str(MOD_ID),
        "target_id": str(target_id),
        "options": {"channel_id": str(channel_id), "count": str(count)},
        "changes": [],
    }
    users = {
        MOD_ID: make_user(guild, MOD_ID, "mod"),
        target_id: make_user(guild, target_id, "author"),
    }
    return AuditLogEntry(
        users=users,
        integrations={},
        app_commands={},
        automod_rules={},
        webhooks={},
        data=data,
        guild=guild,
    )


def make_message(guild: discord.Guild) -> MagicMock:
    message = MagicMock()
    message.guild = guild
    message.channel.id = CHANNEL_ID
    message.channel.mention = f"<#{CHANNEL_ID}>"
    message.author.id = AUTHOR_ID
    message.author.bot = False
    message.author.mention = f"<@{AUTHOR_ID}>"
    message.content = "hello"
    message.attachments = []
    return message


# --------------------------------------------------------------------------- #
# find_message_deleter
# --------------------------------------------------------------------------- #
async def test_a_moderator_deletion_is_attributed(guild, serve):
    serve([delete_entry(guild)])

    deleter = await audit.find_message_deleter(make_message(guild), {})

    assert deleter.id == MOD_ID


async def test_a_self_delete_with_other_entries_present_returns_none(guild, serve):
    """The audit log is never empty in a real server. A self-delete leaves no
    entry of its own, so the lookup runs past the others and returns None."""
    serve([delete_entry(guild, target_id=AUTHOR_ID + 1)])

    assert await audit.find_message_deleter(make_message(guild), {}) is None


async def test_a_repeat_deletion_by_the_same_moderator_counts_as_new(guild, serve):
    """Discord reuses one entry for repeats and bumps its count."""
    cursor: dict[int, tuple[int, int]] = {}
    serve([delete_entry(guild, entry_id=7_000_000_000_000_000_000, count=1)])
    assert (await audit.find_message_deleter(make_message(guild), cursor)).id == MOD_ID

    serve([delete_entry(guild, entry_id=7_000_000_000_000_000_000, count=2)])
    assert (await audit.find_message_deleter(make_message(guild), cursor)).id == MOD_ID


async def test_an_entry_already_used_is_not_claimed_again(guild, serve):
    cursor: dict[int, tuple[int, int]] = {}
    entry = delete_entry(guild, entry_id=7_000_000_000_000_000_000, count=1)
    serve([entry])
    assert (await audit.find_message_deleter(make_message(guild), cursor)).id == MOD_ID

    assert await audit.find_message_deleter(make_message(guild), cursor) is None


async def test_uncached_lookup_returns_the_moderator_and_the_author(guild, serve):
    serve([delete_entry(guild)])

    entry = await audit.find_uncached_deletion(guild, CHANNEL_ID, {})

    assert entry.user.id == MOD_ID
    assert entry.target.id == AUTHOR_ID


# --------------------------------------------------------------------------- #
# The deletion log, end to end
# --------------------------------------------------------------------------- #
async def test_the_deletion_log_posts_when_the_audit_log_is_readable(guild, serve):
    """The reported symptom: with audit log access, nothing was ever logged."""
    serve([delete_entry(guild)])
    cog = moderation.Moderation(MagicMock())
    log_channel = MagicMock()
    log_channel.id = LOG_CHANNEL_ID
    log_channel.send = AsyncMock()
    cog._log_channel = AsyncMock(return_value=log_channel)

    await cog.on_message_delete(make_message(guild))

    log_channel.send.assert_awaited_once()
    text = log_channel.send.await_args.kwargs["embed"].description
    assert f"**Deleted by:** <@{MOD_ID}>" in text
