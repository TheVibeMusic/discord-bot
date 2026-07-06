"""Counting game.

Members count upward in a designated channel, one number per message. Correct
counts get a lightning-bolt reaction. The same member cannot count twice in a
row. Non-numeric messages are ignored, so normal chat is allowed.

Modes:
- hard: a wrong number resets the count to zero.
- easy: a wrong number is marked with an X but the count stands.

Milestones (100, 200, 500, 1000, then every 1000) get a celebration message;
admins can attach a custom image or gif to any specific number.
"""

from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands

import config
from utils import embeds
from utils.permissions import admin_only

log = logging.getLogger("vibe.counting")

VERIFY_EMOJI = "\N{HIGH VOLTAGE SIGN}"  # ⚡
MISS_EMOJI = "\N{CROSS MARK}"  # ❌

FIXED_MILESTONES = {100, 200, 500}

NUMBER_RE = re.compile(r"-?\d+")


def is_milestone(number: int) -> bool:
    return number in FIXED_MILESTONES or (number >= 1000 and number % 1000 == 0)


class Counting(commands.Cog):
    """Channel counting game with hard/easy modes and milestones."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Cache of {guild_id: counting_channel_id} so the message listener
        # can skip non-counting channels without a database query.
        self._counting_channels: dict[int, int] = {}

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_counting (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                mode VARCHAR(10) NOT NULL DEFAULT 'easy',
                current_count INT NOT NULL DEFAULT 0,
                last_user_id BIGINT NULL,
                active BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_counting_milestones (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                number INT NOT NULL,
                media_url VARCHAR(500) NOT NULL,
                UNIQUE KEY unique_milestone (guild_id, number)
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_counting_warnings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_warning (guild_id, user_id)
            )
            """
        )

        rows = await self.db.fetchall("SELECT guild_id, channel_id FROM vibe_counting")
        self._counting_channels = {
            row["guild_id"]: row["channel_id"] for row in rows
        }

    async def _get_game(self, guild_id: int) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM vibe_counting WHERE guild_id = %s", (guild_id,)
        )

    async def _reset_count(self, guild_id: int) -> None:
        """Reset the count to zero and clear all double-count warnings."""
        await self.db.execute(
            "UPDATE vibe_counting SET current_count = 0, last_user_id = NULL "
            "WHERE guild_id = %s",
            (guild_id,),
        )
        await self.db.execute(
            "DELETE FROM vibe_counting_warnings WHERE guild_id = %s", (guild_id,)
        )

    async def _set_mode(self, ctx: commands.Context, mode: str) -> None:
        """Set the counting channel to the current channel with the given mode."""
        await self.db.execute(
            "INSERT INTO vibe_counting (guild_id, channel_id, mode) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id), "
            "mode = VALUES(mode)",
            (ctx.guild.id, ctx.channel.id, mode),
        )
        self._counting_channels[ctx.guild.id] = ctx.channel.id

    # -- Admin commands --------------------------------------------------------

    @commands.command(name="countinghard")
    @admin_only()
    async def counting_hard(self, ctx: commands.Context) -> None:
        """Make this channel the counting channel, hard mode (miscount resets)."""
        await self._set_mode(ctx, "hard")
        await ctx.send(
            embed=embeds.success(
                f"{ctx.channel.mention} is the counting channel — **hard mode**. "
                "A wrong number resets the count. Run `$startgame` to begin."
            )
        )

    @commands.command(name="countingeasy")
    @admin_only()
    async def counting_easy(self, ctx: commands.Context) -> None:
        """Make this channel the counting channel, easy mode (miscounts ignored)."""
        await self._set_mode(ctx, "easy")
        await ctx.send(
            embed=embeds.success(
                f"{ctx.channel.mention} is the counting channel — **easy mode**. "
                "Wrong numbers are marked but don't reset the count. "
                "Run `$startgame` to begin."
            )
        )

    @commands.command(name="startgame")
    @admin_only()
    async def start_game(self, ctx: commands.Context) -> None:
        """Start the counting game (or restart it at zero)."""
        game = await self._get_game(ctx.guild.id)
        if game is None:
            await ctx.send(
                embed=embeds.error(
                    "No counting channel is set. Run `$countingeasy` or "
                    "`$countinghard` in the channel you want first."
                )
            )
            return

        await self._reset_count(ctx.guild.id)
        await self.db.execute(
            "UPDATE vibe_counting SET active = TRUE WHERE guild_id = %s",
            (ctx.guild.id,),
        )
        channel = ctx.guild.get_channel(game["channel_id"])
        target = channel.mention if channel else "the counting channel"
        await ctx.send(
            embed=embeds.success(f"Counting game started in {target}. First number: **1**")
        )

    @commands.command(name="milestone")
    @admin_only()
    async def set_milestone(self, ctx: commands.Context, number: int, url: str) -> None:
        """Attach a custom image/gif URL to a milestone number."""
        if number < 1:
            await ctx.send(embed=embeds.error("Milestone number must be positive."))
            return
        await self.db.execute(
            "INSERT INTO vibe_counting_milestones (guild_id, number, media_url) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE media_url = VALUES(media_url)",
            (ctx.guild.id, number, url),
        )
        await ctx.send(
            embed=embeds.success(f"Custom celebration set for **{number}**.")
        )

    # -- Game listener ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        # Fast path: only the counting channel is ever enforced.
        if self._counting_channels.get(message.guild.id) != message.channel.id:
            return

        # Leave room for admin commands like $startgame.
        if message.content.startswith(config.PREFIX):
            return

        game = await self._get_game(message.guild.id)
        if game is None or not game["active"]:
            return

        # No chatting: a message must contain a number to stay. Words are
        # fine as long as the number is in there somewhere.
        match = NUMBER_RE.search(message.content)
        if match is None:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            reminder = await message.channel.send(
                f"{message.author.mention} no chatting in the counting channel — "
                "your message needs the number in it."
            )
            try:
                await reminder.delete(delay=6)
            except discord.HTTPException:
                pass
            return

        number = int(match.group(0))
        expected = game["current_count"] + 1
        correct = number == expected and message.author.id != game["last_user_id"]

        if correct:
            await self.db.execute(
                "UPDATE vibe_counting SET current_count = %s, last_user_id = %s "
                "WHERE guild_id = %s",
                (number, message.author.id, message.guild.id),
            )
            try:
                await message.add_reaction(VERIFY_EMOJI)
            except discord.HTTPException:
                pass
            if is_milestone(number):
                await self._celebrate(message, number)
            return

        # Miscount. Mark it, then decide the consequence.
        try:
            await message.add_reaction(MISS_EMOJI)
        except discord.HTTPException:
            pass

        # Double-counting has its own warning system in both modes: first
        # offence is a warning, a second offence resets the count to zero.
        if message.author.id == game["last_user_id"]:
            warned = await self.db.fetchone(
                "SELECT 1 FROM vibe_counting_warnings "
                "WHERE guild_id = %s AND user_id = %s",
                (message.guild.id, message.author.id),
            )
            if warned is None:
                await self.db.execute(
                    "INSERT INTO vibe_counting_warnings (guild_id, user_id) "
                    "VALUES (%s, %s)",
                    (message.guild.id, message.author.id),
                )
                await message.channel.send(
                    f"\N{WARNING SIGN} {message.author.mention} you counted twice "
                    "in a row — that one doesn't count. **Don't do it again** "
                    "or else the count resets to zero!"
                )
            else:
                await self._reset_count(message.guild.id)
                await message.channel.send(
                    f"{message.author.mention} counted twice in a row again "
                    "after a warning — the count resets to zero. "
                    "Start again at **1**!"
                )
            return

        # Wrong number from a different member: hard mode resets, easy stands.
        if game["mode"] == "hard":
            await self._reset_count(message.guild.id)
            await message.channel.send(
                f"{message.author.mention} posted **{number}** but the next "
                f"number was **{expected}** — the count resets to zero. "
                "Start again at **1**!"
            )

    async def _celebrate(self, message: discord.Message, number: int) -> None:
        """Post a celebration for a milestone, with custom media if configured."""
        row = await self.db.fetchone(
            "SELECT media_url FROM vibe_counting_milestones "
            "WHERE guild_id = %s AND number = %s",
            (message.guild.id, number),
        )
        embed = embeds.info(
            f"{number:,}!",
            f"The count just hit **{number:,}** — nice work, everyone. "
            f"Next up: **{number + 1}**",
        )
        if row:
            embed.set_image(url=row["media_url"])
        try:
            await message.channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to send milestone message in %s", message.channel.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counting(bot))
