"""Moderation tools: bulk message cleanup and a deletion log.

`$delete <n>` purges the last n messages in the current channel. `$setlog`
configures a channel where deletions are recorded: single deleted messages
are logged with their author and content, and bulk purges are logged as one
summary entry (the individual purged messages are not logged separately).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from utils import embeds
from utils.permissions import admin_only

log = logging.getLogger("vibe.moderation")

PURGE_LIMIT = 100
CONTENT_TRUNCATE = 800
CONFIRM_TIMEOUT_SECONDS = 30


class ConfirmDeleteView(discord.ui.View):
    """Yes/Cancel confirmation shown before a bulk delete runs.

    Only the member who invoked the command can press the buttons. The view
    times out after a short window, after which the prompt is removed.
    """

    def __init__(self, invoker_id: int) -> None:
        super().__init__(timeout=CONFIRM_TIMEOUT_SECONDS)
        self.invoker_id = invoker_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "This confirmation belongs to whoever ran the command.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Yes, delete", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = False
        await interaction.response.defer()
        self.stop()


class Moderation(commands.Cog):
    """Bulk cleanup and deletion logging."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Message ids removed via $delete, so the deletion listener can skip
        # them (the purge is logged once as a summary instead).
        self._purged_ids: set[int] = set()

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_mod_config (
                guild_id BIGINT PRIMARY KEY,
                deletion_log_channel_id BIGINT NULL,
                error_channel_id BIGINT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        # Add the error-channel column to existing installs. Plain ALTER in
        # try/except so it works on both MariaDB and MySQL 8.
        try:
            await self.db.execute(
                "ALTER TABLE vibe_mod_config ADD COLUMN error_channel_id BIGINT NULL"
            )
        except Exception:
            pass  # column already exists

    async def _log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        row = await self.db.fetchone(
            "SELECT deletion_log_channel_id FROM vibe_mod_config WHERE guild_id = %s",
            (guild.id,),
        )
        if not row or not row["deletion_log_channel_id"]:
            return None
        channel = guild.get_channel(row["deletion_log_channel_id"])
        return channel if isinstance(channel, discord.TextChannel) else None

    # -- Commands ------------------------------------------------------------

    @commands.command(name="setlog")
    @admin_only()
    async def set_log(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel where message deletions are logged."""
        await self.db.execute(
            "INSERT INTO vibe_mod_config (guild_id, deletion_log_channel_id) "
            "VALUES (%s, %s) ON DUPLICATE KEY UPDATE "
            "deletion_log_channel_id = VALUES(deletion_log_channel_id)",
            (ctx.guild.id, channel.id),
        )
        await ctx.send(embed=embeds.success(f"Deletion log set to {channel.mention}."))

    @commands.command(name="errorchannel")
    @admin_only()
    async def set_error_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where bot error reports are posted."""
        await self.db.execute(
            "INSERT INTO vibe_mod_config (guild_id, error_channel_id) "
            "VALUES (%s, %s) ON DUPLICATE KEY UPDATE "
            "error_channel_id = VALUES(error_channel_id)",
            (ctx.guild.id, channel.id),
        )
        await ctx.send(embed=embeds.success(f"Bot errors will report to {channel.mention}."))

    @commands.command(name="delete")
    @admin_only()
    async def bulk_delete(self, ctx: commands.Context, amount: int) -> None:
        """Delete the last <amount> messages in this channel (max 100)."""
        if amount < 1 or amount > PURGE_LIMIT:
            await ctx.send(
                embed=embeds.error(f"Amount must be between 1 and {PURGE_LIMIT}.")
            )
            return

        # Ask before deleting — bulk deletes can't be undone.
        view = ConfirmDeleteView(ctx.author.id)
        prompt = await ctx.send(
            embed=embeds.info(
                "Confirm bulk delete",
                f"Delete the last **{amount}** message(s) in {ctx.channel.mention}?",
            ),
            view=view,
        )
        await view.wait()
        try:
            await prompt.delete()
        except discord.HTTPException:
            pass

        if not view.confirmed:
            cancelled = await ctx.send(
                embed=embeds.info(
                    "Cancelled",
                    "No messages were deleted."
                    if view.confirmed is False
                    else "Confirmation timed out — no messages were deleted.",
                )
            )
            await cancelled.delete(delay=5)
            return

        # +1 so the command message itself is removed too. Ids are recorded in
        # the purge check (before deletion happens) because on_message_delete
        # can fire while purge() is still running — marking them afterward
        # would let early deletions slip into the log.
        try:
            deleted = await ctx.channel.purge(
                limit=amount + 1,
                check=lambda m: self._purged_ids.add(m.id) or True,
            )
        except discord.Forbidden:
            await ctx.send(embed=embeds.error("I need the Manage Messages permission here."))
            return

        removed = max(len(deleted) - 1, 0)  # exclude the command message
        confirmation = await ctx.send(
            embed=embeds.success(f"Deleted {removed} message(s).")
        )
        await confirmation.delete(delay=4)

        log_channel = await self._log_channel(ctx.guild)
        if log_channel is not None and log_channel.id != ctx.channel.id:
            await log_channel.send(
                embed=embeds.info(
                    "Bulk delete",
                    f"{ctx.author.mention} deleted {removed} message(s) "
                    f"in {ctx.channel.mention}.",
                )
            )

        if len(self._purged_ids) > 2000:
            self._purged_ids.clear()

    # -- Listener ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        """Log single message deletions to the configured channel."""
        if message.guild is None or message.author.bot:
            return
        if message.id in self._purged_ids:
            self._purged_ids.discard(message.id)
            return

        log_channel = await self._log_channel(message.guild)
        if log_channel is None or log_channel.id == message.channel.id:
            return

        content = message.content or "(no text content)"
        if len(content) > CONTENT_TRUNCATE:
            content = content[:CONTENT_TRUNCATE] + "…"

        embed = embeds.info(
            "Message deleted",
            f"**Author:** {message.author.mention} ({message.author})\n"
            f"**Channel:** {message.channel.mention}\n"
            f"**Content:** {content}",
        )
        if message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join(a.filename for a in message.attachments),
                inline=False,
            )
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException as exc:
            await self.bot.report_error(
                f"Failed to write the deletion log in {message.guild.name}",
                str(exc),
                guild=message.guild,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
