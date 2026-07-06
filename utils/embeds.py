"""Shared helpers for building consistent embeds."""

from __future__ import annotations

import discord

BLUE = discord.Color.blue()
GREEN = discord.Color.green()
RED = discord.Color.red()


def info(title: str, description: str, color: discord.Color = BLUE) -> discord.Embed:
    """Build a standard informational embed."""
    return discord.Embed(title=title, description=description, color=color)


def success(description: str, title: str = "Done") -> discord.Embed:
    return discord.Embed(title=title, description=description, color=GREEN)


def error(description: str, title: str = "Error") -> discord.Embed:
    return discord.Embed(title=title, description=description, color=RED)
