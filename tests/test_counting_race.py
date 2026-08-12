"""Tests that two people counting at once are judged in order.

discord.py handles every message in its own task. Without a lock, two members
posting within a few milliseconds both read the same stored count, and the
second is judged against a stale value — marked wrong, docked ten points, and
in hard mode wiping a run that was actually correct.
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from cogs import counting


GUILD = 1
CHANNEL = 2


class FakeDB:
    """A database whose reads and writes yield, so overlap is guaranteed."""

    def __init__(self, start: int) -> None:
        self.count = start
        self.last_user_id = None
        self.reads = 0

    async def fetchone(self, query, params=()):
        # Match the suffixed tables first — "FROM vibe_counting" is a prefix of
        # vibe_counting_stats, _blocks and _warnings.
        if "vibe_counting_blocks" in query:
            return None  # nobody is blocked
        if "vibe_counting_stats" in query or "vibe_counting_warnings" in query:
            return None  # no leaderboard rows, no prior warnings
        if "FROM vibe_counting" in query:
            self.reads += 1
            await asyncio.sleep(0)  # let the other task interleave here
            return {
                "guild_id": GUILD,
                "channel_id": CHANNEL,
                "mode": "hard",
                "current_count": self.count,
                "last_user_id": self.last_user_id,
                "active": True,
                "prize_chance": 10,
                "roast_chance": 0,
            }
        return None

    async def fetchall(self, query, params=()):
        return []

    async def execute(self, query, params=()):
        await asyncio.sleep(0)
        # The reset also starts "UPDATE vibe_counting SET current_count", so it
        # has to be recognised first — it takes only the guild id.
        if "current_count = 0" in query:
            self.count, self.last_user_id = 0, None
        elif query.startswith("UPDATE vibe_counting SET current_count"):
            self.count, self.last_user_id = params[0], params[1]
        return 1


def make_cog(db: FakeDB) -> counting.Counting:
    cog = counting.Counting(MagicMock())
    cog.bot.db = db
    cog.bot.report_error = AsyncMock()
    cog._counting_channels = {GUILD: CHANNEL}
    cog._milestone_numbers = {}
    return cog


def make_message(author_id: int, content: str) -> MagicMock:
    msg = MagicMock()
    msg.id = 9000 + author_id
    msg.guild.id = GUILD
    msg.guild.name = "Test"
    msg.author.id = author_id
    msg.author.bot = False
    msg.author.mention = f"<@{author_id}>"
    msg.channel.id = CHANNEL
    msg.channel.send = AsyncMock()
    msg.add_reaction = AsyncMock()
    msg.delete = AsyncMock()
    msg.content = content
    return msg


async def test_two_correct_counts_at_once_are_both_accepted():
    """X posts 41 and Y posts 42 simultaneously. Both are right in sequence,
    so neither should be penalised and the count should land on 42."""
    db = FakeDB(start=40)
    cog = make_cog(db)
    cog._offer_gamble = AsyncMock()

    first = make_message(101, "41")
    second = make_message(202, "42")
    await asyncio.gather(cog.on_message(first), cog.on_message(second))

    assert db.count == 42, f"count ended at {db.count}, expected 42"
    cog._offer_gamble.assert_not_awaited()  # nobody was treated as wrong
    for msg in (first, second):
        msg.add_reaction.assert_awaited_once_with(counting.VERIFY_EMOJI)


async def test_the_run_is_not_wiped_by_a_simultaneous_correct_count():
    """The damaging version: in hard mode a stale read resets everyone."""
    db = FakeDB(start=98)
    cog = make_cog(db)
    cog._offer_gamble = AsyncMock()

    await asyncio.gather(
        cog.on_message(make_message(101, "99")),
        cog.on_message(make_message(202, "100")),
    )

    assert db.count == 100, f"run was wiped to {db.count}"


async def test_a_genuinely_wrong_number_is_still_caught():
    """The lock must not make the game accept anything."""
    db = FakeDB(start=40)
    cog = make_cog(db)
    cog._offer_gamble = AsyncMock()

    wrong = make_message(101, "77")
    await cog.on_message(wrong)

    wrong.add_reaction.assert_awaited_once_with(counting.MISS_EMOJI)
    assert db.count == 0  # hard mode reset


async def test_the_same_member_still_cannot_count_twice_in_a_row():
    db = FakeDB(start=40)
    cog = make_cog(db)
    cog._offer_gamble = AsyncMock()

    await cog.on_message(make_message(101, "41"))
    assert db.count == 41

    repeat = make_message(101, "42")
    await cog.on_message(repeat)
    repeat.add_reaction.assert_awaited_once_with(counting.MISS_EMOJI)


def test_each_guild_gets_its_own_lock():
    """One busy server must not stall another."""
    cog = make_cog(FakeDB(start=0))
    assert cog._guild_lock(1) is cog._guild_lock(1)
    assert cog._guild_lock(1) is not cog._guild_lock(2)
