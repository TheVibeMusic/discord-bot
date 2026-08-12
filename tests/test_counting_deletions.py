"""Tests for the counting game's self-delete rule.

Deleting your own count is disruptive: the next counter reads the channel,
sees the wrong last number, and takes the penalty for somebody else's
deletion. Deleting is deliberate, so there is no warning step — every offence
blocks and posts a shaming image, escalating from two hours to a day to
permanent.

The guards matter as much as the punishment — the bot's own housekeeping
deletions, moderator cleanup, exempt staff, and a missing View Audit Log
permission must all leave members alone.
"""

from __future__ import annotations

import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock

import config
from cogs import counting


GUILD = 1
CHANNEL = 2
MEMBER = 111111111111111111
EXEMPT = 966507927756234823
MOD = 999

ACTIVE_GAME = {
    "guild_id": GUILD,
    "channel_id": CHANNEL,
    "mode": "hard",
    "current_count": 40,
    "active": True,
}


@pytest.fixture(autouse=True)
def exempt_list(monkeypatch):
    monkeypatch.setattr(config, "COUNTING_EXEMPT_IDS", {EXEMPT})


@pytest.fixture
def cog(monkeypatch) -> counting.Counting:
    instance = counting.Counting(MagicMock())
    instance.bot.db = MagicMock()
    instance.bot.db.fetchone = AsyncMock(return_value=ACTIVE_GAME)
    instance.bot.db.execute = AsyncMock(return_value=1)
    instance.bot.db.fetchall = AsyncMock(return_value=[])  # no shaming images
    instance._counting_channels = {GUILD: CHANNEL}

    # By default the audit log is readable and shows no other deleter, which
    # means the author removed their own message.
    monkeypatch.setattr(counting, "find_message_deleter", AsyncMock(return_value=None))
    return instance


def make_message(
    *, author_id: int = MEMBER, content: str = "41", channel_id: int = CHANNEL,
    is_bot: bool = False, message_id: int = 5000,
) -> MagicMock:
    msg = MagicMock()
    msg.id = message_id
    msg.guild.id = GUILD
    msg.guild.name = "Test"
    msg.author.id = author_id
    msg.author.bot = is_bot
    msg.author.mention = f"<@{author_id}>"
    msg.channel.id = channel_id
    msg.channel.send = AsyncMock()
    msg.content = content
    return msg


# --------------------------------------------------------------------------- #
# The escalating ladder: two hours, then a day, then forever
# --------------------------------------------------------------------------- #
def block_write(cog):
    """The INSERT that recorded the block: (sql, params)."""
    for call in cog.bot.db.execute.await_args_list:
        if "vibe_counting_blocks" in call.args[0]:
            return call.args[0], call.args[1]
    raise AssertionError("no block was written")


async def test_first_offence_blocks_for_two_hours(cog):
    cog.bot.db.fetchone = AsyncMock(return_value=None)  # no strikes yet
    msg = make_message()

    await cog._register_deletion(msg)

    sql, params = block_write(cog)
    assert params[2] == 1  # strikes
    assert "FALSE" in sql  # not permanent
    until = params[3]
    minutes = (until - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).total_seconds() / 60
    assert 118 < minutes < 121, f"blocked for {minutes:.0f} minutes, expected ~120"


async def test_second_offence_blocks_for_a_day(cog):
    cog.bot.db.fetchone = AsyncMock(return_value={"strikes": 1})
    msg = make_message()

    await cog._register_deletion(msg)

    _, params = block_write(cog)
    assert params[2] == 2
    minutes = (params[3] - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).total_seconds() / 60
    assert 1438 < minutes < 1441, f"blocked for {minutes:.0f} minutes, expected ~1440"


async def test_third_offence_is_permanent(cog):
    cog.bot.db.fetchone = AsyncMock(return_value={"strikes": 2})
    msg = make_message()

    await cog._register_deletion(msg)

    sql, params = block_write(cog)
    assert params[2] == 3
    assert "permanent = TRUE" in sql
    assert "permanently" in msg.channel.send.await_args_list[0].args[0]


async def test_further_offences_stay_permanent(cog):
    """The ladder must not run off the end of the list."""
    cog.bot.db.fetchone = AsyncMock(return_value={"strikes": 9})
    await cog._register_deletion(make_message())

    sql, params = block_write(cog)
    assert params[2] == 10
    assert "permanent = TRUE" in sql


async def test_every_offence_posts_a_shaming_image(cog):
    cog.bot.db.fetchone = AsyncMock(return_value=None)
    cog.bot.db.fetchall = AsyncMock(return_value=[{"media_url": "https://x.test/a.gif"}])
    msg = make_message()

    await cog._register_deletion(msg)

    embeds_sent = [
        c.kwargs["embed"] for c in msg.channel.send.await_args_list if "embed" in c.kwargs
    ]
    assert len(embeds_sent) == 1
    assert embeds_sent[0].image.url == "https://x.test/a.gif"
    assert embeds_sent[0].description is None  # images only, no bot text


async def test_no_shaming_images_configured_is_fine(cog):
    cog.bot.db.fetchone = AsyncMock(return_value=None)
    cog.bot.db.fetchall = AsyncMock(return_value=[])
    msg = make_message()

    await cog._register_deletion(msg)  # must not raise

    assert msg.channel.send.await_count == 1  # just the block notice


async def test_block_message_only_pings_the_offender(cog):
    cog.bot.db.fetchone = AsyncMock(return_value={"strikes": 1})
    msg = make_message()

    await cog._register_deletion(msg)

    allowed = msg.channel.send.await_args_list[0].kwargs["allowed_mentions"]
    assert allowed.everyone is False
    assert allowed.roles is False
    assert [o.id for o in allowed.users] == [MEMBER]


# --------------------------------------------------------------------------- #
# Guards: who must NOT be punished
# --------------------------------------------------------------------------- #
async def test_bot_own_housekeeping_deletion_is_ignored(cog):
    """The no-chat rule deletes messages itself; that isn't the member's doing."""
    msg = make_message(content="hello")
    cog._bot_deleted.add(msg.id)
    cog._register_deletion = AsyncMock()

    await cog.on_message_delete(msg)

    cog._register_deletion.assert_not_awaited()
    assert msg.id not in cog._bot_deleted  # consumed


async def test_moderator_deletion_is_ignored(cog, monkeypatch):
    monkeypatch.setattr(
        counting, "find_message_deleter", AsyncMock(return_value=MagicMock(id=MOD))
    )
    cog._register_deletion = AsyncMock()

    await cog.on_message_delete(make_message())

    cog._register_deletion.assert_not_awaited()


async def test_exempt_staff_are_ignored(cog):
    cog._register_deletion = AsyncMock()
    await cog.on_message_delete(make_message(author_id=EXEMPT))
    cog._register_deletion.assert_not_awaited()


async def test_missing_audit_permission_stays_quiet(cog, monkeypatch):
    """Without the permission every deletion looks like a self-delete, so the
    rule must not blame anyone at random."""
    monkeypatch.setattr(
        counting, "find_message_deleter", AsyncMock(return_value=counting.UNKNOWN)
    )
    cog._register_deletion = AsyncMock()

    await cog.on_message_delete(make_message())

    cog._register_deletion.assert_not_awaited()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"is_bot": True},                  # the bot's own messages
        {"channel_id": 8888},              # a different channel
        {"content": "just chatting"},      # never a count attempt
        {"content": "$startgame"},         # an admin command
    ],
)
async def test_irrelevant_deletions_are_ignored(cog, kwargs):
    cog._register_deletion = AsyncMock()
    await cog.on_message_delete(make_message(**kwargs))
    cog._register_deletion.assert_not_awaited()


async def test_a_real_self_delete_is_punished(cog):
    """The positive case, so the guards above aren't just blocking everything."""
    cog._register_deletion = AsyncMock()
    await cog.on_message_delete(make_message())
    cog._register_deletion.assert_awaited_once()


async def test_inactive_game_is_ignored(cog):
    cog.bot.db.fetchone = AsyncMock(return_value={**ACTIVE_GAME, "active": False})
    cog._register_deletion = AsyncMock()

    await cog.on_message_delete(make_message())

    cog._register_deletion.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Serving a block
# --------------------------------------------------------------------------- #
async def test_timed_block_reports_its_expiry(cog):
    later = datetime.datetime(2030, 1, 1)
    cog.bot.db.fetchone = AsyncMock(
        return_value={"blocked_until": later, "permanent": 0}
    )
    assert await cog._block_state(GUILD, MEMBER) == (True, later)


async def test_permanent_block_reports_no_expiry(cog):
    cog.bot.db.fetchone = AsyncMock(
        return_value={"blocked_until": None, "permanent": 1}
    )
    assert await cog._block_state(GUILD, MEMBER) == (True, None)


async def test_unblocked_member_is_free(cog):
    """The query filters expired blocks out, so no row means free to play."""
    cog.bot.db.fetchone = AsyncMock(return_value=None)
    assert await cog._block_state(GUILD, MEMBER) == (False, None)


def test_the_ladder_matches_the_agreed_punishments():
    assert counting.BLOCK_LADDER == (120, 1440, None)
