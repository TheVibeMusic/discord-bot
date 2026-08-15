"""Shared helpers for building consistent embeds."""

from __future__ import annotations

import discord

BLUE = discord.Color.blue()
GREEN = discord.Color.green()
RED = discord.Color.red()


# Discord rejects an embed whose description is longer than this.
DESCRIPTION_LIMIT = 4096


def info(title: str, description: str, color: discord.Color = BLUE) -> discord.Embed:
    """Build a standard informational embed."""
    return discord.Embed(title=title, description=description, color=color)


def paged(
    title: str,
    lines: list[str],
    *,
    color: discord.Color = BLUE,
    limit: int = DESCRIPTION_LIMIT,
) -> list[discord.Embed]:
    """Split lines across as many embeds as the description limit requires.

    The listing commands ($milestones, $roasts, $shames, $bdaycards) grow with
    whatever admins add, and each is the only place the removal ids appear — so
    overflowing the limit would break the very command needed to trim the list
    back down. A line longer than the limit on its own is truncated rather than
    dropped, since losing an id silently is worse than an ugly line.
    """
    pages: list[discord.Embed] = []
    current: list[str] = []
    length = 0

    for line in lines:
        if len(line) > limit:
            line = line[: limit - 1] + "\N{HORIZONTAL ELLIPSIS}"
        # +1 for the newline joining this line to the previous one.
        addition = len(line) + (1 if current else 0)
        if length + addition > limit:
            pages.append(info(title, "\n".join(current), color))
            current, length = [line], len(line)
        else:
            current.append(line)
            length += addition

    if current:
        pages.append(info(title, "\n".join(current), color))
    if len(pages) > 1:
        for number, embed in enumerate(pages, start=1):
            embed.set_footer(text=f"Page {number} of {len(pages)}")
    return pages


def success(description: str, title: str = "Done") -> discord.Embed:
    return discord.Embed(title=title, description=description, color=GREEN)


def error(description: str, title: str = "Error") -> discord.Embed:
    return discord.Embed(title=title, description=description, color=RED)
