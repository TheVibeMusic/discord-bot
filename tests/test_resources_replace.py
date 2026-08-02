"""Tests for $replaceresource.

The command used to infer "does this resource exist?" from the UPDATE
rowcount, but an UPDATE affects zero rows when the new URL already matches
the stored one — so re-pasting a link reported the resource as missing.
Existence is now checked separately. Same bug class as $roastchance and
$milestonechance.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from cogs import resources


GUILD = 1


def build(existing_row: dict | None, update_rowcount: int):
    """A cog and ctx wired to a given lookup result and UPDATE result."""
    cog = resources.Resources(MagicMock())
    cog.bot.db = MagicMock()
    cog.bot.db.fetchone = AsyncMock(return_value=existing_row)
    cog.bot.db.execute = AsyncMock(return_value=update_rowcount)

    ctx = MagicMock()
    ctx.guild.id = GUILD
    ctx.send = AsyncMock()
    return cog, ctx


def sent_embed(ctx):
    return ctx.send.await_args.kwargs["embed"]


# --------------------------------------------------------------------------- #
# The bug: replacing a link with the URL it already has
# --------------------------------------------------------------------------- #
async def test_replacing_with_the_same_url_still_succeeds():
    """UPDATE affects 0 rows, but the resource is right there."""
    cog, ctx = build({"1": 1}, update_rowcount=0)
    await cog.replace_resource.callback(
        cog, ctx, entry="[TWITCH](https://twitch.tv/theaiumbrella)"
    )

    assert sent_embed(ctx).title == "Done"
    cog.bot.db.execute.assert_awaited_once()


async def test_replacing_with_a_new_url_succeeds():
    cog, ctx = build({"1": 1}, update_rowcount=1)
    await cog.replace_resource.callback(
        cog, ctx, entry="[TWITCH](https://twitch.tv/somewhere-else)"
    )

    assert sent_embed(ctx).title == "Done"


# --------------------------------------------------------------------------- #
# A genuinely missing resource must still error
# --------------------------------------------------------------------------- #
async def test_missing_resource_still_errors():
    cog, ctx = build(None, update_rowcount=0)
    await cog.replace_resource.callback(
        cog, ctx, entry="[NOPE](https://example.test)"
    )

    embed = sent_embed(ctx)
    assert embed.title == "Error"
    assert "No resource named" in embed.description
    cog.bot.db.execute.assert_not_awaited()  # nothing written


# --------------------------------------------------------------------------- #
# Input parsing is unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "entry",
    ["not a link at all", "[NAME](ftp://example.test)", "[NAME](example.test)"],
)
async def test_unparseable_entry_errors_before_touching_the_database(entry):
    cog, ctx = build({"1": 1}, update_rowcount=1)
    await cog.replace_resource.callback(cog, ctx, entry=entry)

    assert sent_embed(ctx).title == "Error"
    cog.bot.db.fetchone.assert_not_awaited()
    cog.bot.db.execute.assert_not_awaited()


def test_parse_resource_accepts_both_supported_formats():
    assert resources.parse_resource("[NAME](https://example.test)") == (
        "NAME",
        "https://example.test",
    )
    assert resources.parse_resource("NAME https://example.test") == (
        "NAME",
        "https://example.test",
    )
    assert resources.parse_resource("just some words") is None
