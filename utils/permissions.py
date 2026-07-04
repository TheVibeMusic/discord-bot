"""Permission helpers and command checks based on environment configuration.

Access tiers:
- manager: owner-level access to every function (bot managers and server owner)
- admin:   managers, the admin role, and anyone listed in ADMIN_IDS
- mod:     admins, the mod role, and anyone listed in MOD_IDS
"""

from __future__ import annotations

import discord
from discord.ext import commands

import config


def _has_role(member: discord.Member, role_id: int) -> bool:
    return role_id != 0 and any(role.id == role_id for role in member.roles)


def is_manager(user_id: int) -> bool:
    """Return True for owner-level users (bot managers and the server owner)."""
    return user_id in config.MANAGER_IDS or user_id == config.OWNER_ID


def is_admin(member: discord.Member) -> bool:
    """Return True if the member has admin access or higher."""
    return (
        is_manager(member.id)
        or member.id in config.ADMIN_IDS
        or _has_role(member, config.ADMIN_ROLE_ID)
    )


def is_mod(member: discord.Member) -> bool:
    """Return True if the member has mod access or higher."""
    return (
        is_admin(member)
        or member.id in config.MOD_IDS
        or _has_role(member, config.MOD_ROLE_ID)
    )


def admin_only():
    """Command check allowing admins and above."""

    async def predicate(ctx: commands.Context) -> bool:
        return isinstance(ctx.author, discord.Member) and is_admin(ctx.author)

    return commands.check(predicate)


def mod_only():
    """Command check allowing mods and above."""

    async def predicate(ctx: commands.Context) -> bool:
        return isinstance(ctx.author, discord.Member) and is_mod(ctx.author)

    return commands.check(predicate)
