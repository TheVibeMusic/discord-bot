"""Tests for Counting._should_celebrate — which numbers fire a celebration.

Permanent milestones (100, 200, 500, every 1000) always fire. Any other number
an admin attached an image to is a "prize" number and fires only a percentage
of the time, so numbers the count passes repeatedly stay a surprise.

The dice are monkeypatched so every case is deterministic.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from cogs import counting


GUILD = 1


@pytest.fixture
def cog() -> counting.Counting:
    instance = counting.Counting(MagicMock())
    # Prize numbers configured for this guild (as $milestone would cache them).
    instance._milestone_numbers = {GUILD: {25, 150}}
    return instance


@pytest.fixture
def roll(monkeypatch):
    """Force the next dice roll to a chosen value."""

    def _set(value: int) -> None:
        monkeypatch.setattr(counting.random, "randint", lambda a, b: value)

    return _set


# --------------------------------------------------------------------------- #
# Permanent milestones
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("number", [100, 200, 500, 1000, 2000, 10000])
def test_permanent_milestones_always_fire(cog, roll, number):
    roll(100)  # worst possible roll
    assert cog._should_celebrate(GUILD, number, prize_chance=1) is True


def test_permanent_milestone_fires_even_without_an_image(cog, roll):
    roll(100)
    cog._milestone_numbers = {GUILD: set()}  # no images configured at all
    assert cog._should_celebrate(GUILD, 100, prize_chance=10) is True


@pytest.mark.parametrize("number", [99, 101, 999, 1001, 1500])
def test_near_misses_are_not_permanent(cog, roll, number):
    """Numbers adjacent to a permanent milestone are not themselves permanent."""
    roll(100)
    assert cog._should_celebrate(GUILD, number, prize_chance=10) is False


# --------------------------------------------------------------------------- #
# Prize numbers
# --------------------------------------------------------------------------- #
def test_prize_fires_when_the_roll_is_under_the_chance(cog, roll):
    roll(10)
    assert cog._should_celebrate(GUILD, 25, prize_chance=10) is True


def test_prize_does_not_fire_when_the_roll_is_over_the_chance(cog, roll):
    roll(11)
    assert cog._should_celebrate(GUILD, 25, prize_chance=10) is False


def test_prize_always_fires_at_one_hundred_percent(cog, roll):
    roll(100)
    assert cog._should_celebrate(GUILD, 150, prize_chance=100) is True


def test_prize_boundary_is_inclusive(cog, roll):
    """A roll exactly equal to the chance counts as a hit."""
    roll(10)
    assert cog._should_celebrate(GUILD, 150, prize_chance=10) is True


# --------------------------------------------------------------------------- #
# Numbers that are neither
# --------------------------------------------------------------------------- #
def test_ordinary_number_never_fires(cog, roll):
    roll(1)  # best possible roll
    assert cog._should_celebrate(GUILD, 37, prize_chance=100) is False


def test_prize_from_another_guild_does_not_fire_here(cog, roll):
    """The prize cache is per-guild; 25 belongs to GUILD, not guild 2."""
    roll(1)
    assert cog._should_celebrate(2, 25, prize_chance=100) is False


def test_guild_with_no_cache_entry_is_safe(cog, roll):
    roll(1)
    cog._milestone_numbers = {}
    assert cog._should_celebrate(GUILD, 25, prize_chance=100) is False


# --------------------------------------------------------------------------- #
# Distribution sanity (real dice, not monkeypatched)
# --------------------------------------------------------------------------- #
def test_prize_fires_roughly_at_the_configured_rate(cog):
    """A loose statistical check that the percentage is actually applied."""
    hits = sum(
        cog._should_celebrate(GUILD, 25, prize_chance=10) for _ in range(10_000)
    )
    assert 700 < hits < 1300  # ~10% of 10,000, generous margin


def test_permanent_never_depends_on_the_dice(cog):
    assert all(
        cog._should_celebrate(GUILD, 100, prize_chance=1) for _ in range(1_000)
    )
