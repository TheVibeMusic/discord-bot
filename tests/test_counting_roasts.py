"""Tests for Counting._maybe_roast — the image reply to a miscount.

Roasts are images only; the bot writes no text of its own. They fire only a
percentage of the time so a miss gets teased rather than piled on, and a
chance of zero switches them off without deleting the images.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from cogs import counting


GUILD = 1
ROASTS = [{"media_url": "https://example.test/a.gif"},
          {"media_url": "https://example.test/b.gif"}]


@pytest.fixture
def cog() -> counting.Counting:
    instance = counting.Counting(MagicMock())
    instance.bot.db = MagicMock()
    instance.bot.db.fetchall = AsyncMock(return_value=ROASTS)
    return instance


@pytest.fixture
def message() -> MagicMock:
    fake = MagicMock()
    fake.guild.id = GUILD
    fake.channel.send = AsyncMock()
    return fake


@pytest.fixture
def roll(monkeypatch):
    """Force the next dice roll to a chosen value."""

    def _set(value: int) -> None:
        monkeypatch.setattr(counting.random, "randint", lambda a, b: value)

    return _set


# --------------------------------------------------------------------------- #
# When a roast fires
# --------------------------------------------------------------------------- #
async def test_roast_posts_when_the_roll_is_under_the_chance(cog, message, roll):
    roll(25)
    await cog._maybe_roast(message, roast_chance=25)
    message.channel.send.assert_awaited_once()


async def test_roast_posts_an_image_only_with_no_bot_text(cog, message, roll):
    roll(1)
    await cog._maybe_roast(message, roast_chance=100)

    kwargs = message.channel.send.await_args.kwargs
    embed = kwargs["embed"]
    assert "content" not in kwargs or kwargs.get("content") is None
    assert embed.image.url in {r["media_url"] for r in ROASTS}
    assert embed.description is None
    assert embed.title is None


async def test_roast_picks_from_the_whole_pool(cog, message, monkeypatch):
    """Every configured image should be reachable, not just the first."""
    monkeypatch.setattr(counting.random, "randint", lambda a, b: 1)
    seen = set()
    for _ in range(200):
        message.channel.send.reset_mock()
        await cog._maybe_roast(message, roast_chance=100)
        seen.add(message.channel.send.await_args.kwargs["embed"].image.url)
    assert seen == {r["media_url"] for r in ROASTS}


# --------------------------------------------------------------------------- #
# When it stays quiet
# --------------------------------------------------------------------------- #
async def test_no_roast_when_the_roll_is_over_the_chance(cog, message, roll):
    roll(26)
    await cog._maybe_roast(message, roast_chance=25)
    message.channel.send.assert_not_awaited()


async def test_zero_chance_turns_roasts_off(cog, message, roll):
    roll(1)  # best possible roll
    await cog._maybe_roast(message, roast_chance=0)
    message.channel.send.assert_not_awaited()


async def test_zero_chance_does_not_even_hit_the_database(cog, message, roll):
    roll(1)
    await cog._maybe_roast(message, roast_chance=0)
    cog.bot.db.fetchall.assert_not_awaited()


async def test_no_images_configured_posts_nothing(cog, message, roll):
    roll(1)
    cog.bot.db.fetchall = AsyncMock(return_value=[])
    await cog._maybe_roast(message, roast_chance=100)
    message.channel.send.assert_not_awaited()


async def test_boundary_roll_equal_to_chance_fires(cog, message, roll):
    roll(25)
    await cog._maybe_roast(message, roast_chance=25)
    message.channel.send.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Resilience
# --------------------------------------------------------------------------- #
async def test_a_failed_send_does_not_raise(cog, message, roll):
    """A broken image URL or a permissions problem must not break the count."""
    import discord

    roll(1)
    message.channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
    await cog._maybe_roast(message, roast_chance=100)  # must not raise


async def test_fires_roughly_at_the_configured_rate(cog, message):
    """Loose statistical check that the percentage is actually applied."""
    fired = 0
    for _ in range(2_000):
        message.channel.send.reset_mock()
        await cog._maybe_roast(message, roast_chance=25)
        fired += message.channel.send.await_count
    assert 350 < fired < 650  # ~25% of 2,000, generous margin
