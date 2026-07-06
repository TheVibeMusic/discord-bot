"""General-purpose and bot-management commands."""

from __future__ import annotations

from discord.ext import commands

from utils import embeds
from utils.permissions import admin_only


class General(commands.Cog):
    """Health checks and bot management."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Report the bot's current gateway latency."""
        latency_ms = round(self.bot.latency * 1000)
        await ctx.send(f"Pong! {latency_ms}ms")

    @commands.command(name="sync")
    @admin_only()
    async def sync_commands(self, ctx: commands.Context) -> None:
        """Re-sync slash commands with Discord.

        Clears any guild-scoped duplicates first, then syncs the global
        command tree. Useful when slash commands look stale or missing.
        """
        status = await ctx.send(embed=embeds.info("Sync", "Syncing slash commands..."))
        try:
            self.bot.tree.clear_commands(guild=ctx.guild)
            await self.bot.tree.sync(guild=ctx.guild)
            synced = await self.bot.tree.sync()
        except Exception as exc:  # surfaced to the invoker; rare and varied causes
            await status.edit(embed=embeds.error(f"Sync failed: {exc}"))
            return
        await status.edit(
            embed=embeds.success(
                f"Synced {len(synced)} slash command(s) globally. "
                "Discord can take a minute (and a Ctrl+R) to show changes."
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
