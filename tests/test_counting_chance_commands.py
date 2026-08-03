"""Tests for $roastchance and $milestonechance.

Both once inferred "is this guild set up?" from the UPDATE rowcount, but an
UPDATE reports zero affected rows when the value already equals what was
asked for. Setting a chance to the value it already had therefore claimed no
counting channel was configured. Existence is now checked separately.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from cogs import counting


GUILD = 1
EXISTING_GAME = {
    "guild_id": GUILD,
    "channel_id": 2,
    "mode": "hard",
    "current_count": 40,
    "prize_chance": 10,
    "roast_chance": 25,
}


def build(game: dict | None, update_rowcount: int):
    """A cog and ctx wired to a given game row and UPDATE result."""
    cog = counting.Counting(MagicMock())
    cog.bot.db = MagicMock()
    cog.bot.db.fetchone = AsyncMock(return_value=game)
    cog.bot.db.execute = AsyncMock(return_value=update_rowcount)

    ctx = MagicMock()
    ctx.guild.id = GUILD
    ctx.send = AsyncMock()
    return cog, ctx


def sent_embed(ctx):
    return ctx.send.await_args.kwargs["embed"]


# --------------------------------------------------------------------------- #
# Tyro's case: setting the same value it already had
# --------------------------------------------------------------------------- #
async def test_roastchance_unchanged_value_still_succeeds():
    """UPDATE affects 0 rows, but the guild IS configured."""
    cog, ctx = build(EXISTING_GAME, update_rowcount=0)
    await cog.set_roast_chance.callback(cog, ctx, 25)

    assert sent_embed(ctx).title == "Done"
    cog.bot.db.execute.assert_awaited_once()


async def test_milestonechance_unchanged_value_still_succeeds():
    cog, ctx = build(EXISTING_GAME, update_rowcount=0)
    await cog.set_prize_chance.callback(cog, ctx, 10)

    assert sent_embed(ctx).title == "Done"
    cog.bot.db.execute.assert_awaited_once()


# --------------------------------------------------------------------------- #
# The genuine "not set up" case must still be caught
# --------------------------------------------------------------------------- #
async def test_roastchance_errors_when_no_game_exists():
    cog, ctx = build(None, update_rowcount=0)
    await cog.set_roast_chance.callback(cog, ctx, 25)

    assert sent_embed(ctx).title == "Error"
    assert "No counting channel" in sent_embed(ctx).description
    cog.bot.db.execute.assert_not_awaited()  # nothing written


async def test_milestonechance_errors_when_no_game_exists():
    cog, ctx = build(None, update_rowcount=0)
    await cog.set_prize_chance.callback(cog, ctx, 50)

    assert sent_embed(ctx).title == "Error"
    cog.bot.db.execute.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Normal changes and range checks
# --------------------------------------------------------------------------- #
async def test_roastchance_changed_value_succeeds():
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    await cog.set_roast_chance.callback(cog, ctx, 40)
    assert sent_embed(ctx).title == "Done"


async def test_roastchance_zero_switches_roasts_off():
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    await cog.set_roast_chance.callback(cog, ctx, 0)

    embed = sent_embed(ctx)
    assert embed.title == "Done"
    assert "off" in embed.description.lower()


@pytest.mark.parametrize("percent", [-1, 101, 500])
async def test_roastchance_rejects_out_of_range(percent):
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    await cog.set_roast_chance.callback(cog, ctx, percent)

    assert sent_embed(ctx).title == "Error"
    cog.bot.db.execute.assert_not_awaited()


@pytest.mark.parametrize("percent", [0, 101])
async def test_milestonechance_rejects_out_of_range(percent):
    """Prizes have no 'off' switch, so zero is out of range here."""
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    await cog.set_prize_chance.callback(cog, ctx, percent)

    assert sent_embed(ctx).title == "Error"
    cog.bot.db.execute.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Image links on $roast and $milestone
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("garbage", ["banana", "imgur.com/a.png", "https://imgur.com/gallery/x"])
async def test_roast_rejects_a_non_image_link(garbage):
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    await cog.add_roast.callback(cog, ctx, garbage)

    assert sent_embed(ctx).title == "Error"
    cog.bot.db.execute.assert_not_awaited()  # garbage never reaches the table


@pytest.mark.parametrize("garbage", ["banana", "https://imgur.com/gallery/x"])
async def test_milestone_rejects_a_non_image_link(garbage):
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    await cog.set_milestone.callback(cog, ctx, 150, garbage)

    assert sent_embed(ctx).title == "Error"
    cog.bot.db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [
        "https://i.imgur.com/abc.png",
        "<https://i.imgur.com/abc.png>",  # preview suppressed with <>
    ],
)
async def test_roast_accepts_a_real_image_link(url):
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    cog.bot.db.fetchone = AsyncMock(return_value={"c": 1})
    await cog.add_roast.callback(cog, ctx, url)

    assert sent_embed(ctx).title == "Done"
    stored = cog.bot.db.execute.await_args.args[1][1]
    assert stored == "https://i.imgur.com/abc.png"  # brackets stripped


async def test_milestone_accepts_a_bracketed_link():
    cog, ctx = build(EXISTING_GAME, update_rowcount=1)
    cog.bot.db.fetchone = AsyncMock(return_value={"c": 1})
    await cog.set_milestone.callback(cog, ctx, 150, "<https://i.imgur.com/abc.gif>")

    assert sent_embed(ctx).title == "Done"
    stored = cog.bot.db.execute.await_args.args[1][2]
    assert stored == "https://i.imgur.com/abc.gif"
