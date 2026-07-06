"""Ticket system: private staff-support channels opened from a button panel.

Members click a persistent "Open Ticket" button to create a private channel
visible only to them and the configured staff role. Staff (or the opener)
close the ticket with a button or the close command; a plain-text transcript
is posted to a configured log channel before the channel is deleted.

The panel and close buttons use fixed custom_ids with timeout=None and are
re-registered on startup, so they keep working across bot restarts.
"""

from __future__ import annotations

import asyncio
import io
import logging

import discord
from discord.ext import commands

from utils import embeds
from utils.permissions import admin_only, is_admin, is_mod

log = logging.getLogger("vibe.tickets")

OPEN_BUTTON_ID = "vibe_ticket_open"
CLOSE_BUTTON_ID = "vibe_ticket_close"

TRANSCRIPT_MESSAGE_LIMIT = 1000
DELETE_DELAY_SECONDS = 5


class TicketPanelView(discord.ui.View):
    """Persistent view holding the panel's "Open Ticket" button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket",
        style=discord.ButtonStyle.primary,
        custom_id=OPEN_BUTTON_ID,
    )
    async def open_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        cog = interaction.client.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message(
                "Tickets are unavailable right now. Please try again shortly.",
                ephemeral=True,
            )
            return
        await cog.open_ticket(interaction)


class TicketCloseView(discord.ui.View):
    """Persistent view holding the in-ticket "Close Ticket" button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_BUTTON_ID,
    )
    async def close_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        cog = interaction.client.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message(
                "Tickets are unavailable right now. Please try again shortly.",
                ephemeral=True,
            )
            return
        await cog.close_from_button(interaction)


class Tickets(commands.Cog):
    """Private staff-support tickets."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self) -> None:
        """Create tables and register persistent views."""
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_ticket_config (
                guild_id BIGINT PRIMARY KEY,
                staff_role_id BIGINT NULL,
                category_id BIGINT NULL,
                log_channel_id BIGINT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_tickets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                opener_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP NULL,
                closed_by BIGINT NULL,
                INDEX idx_open (guild_id, opener_id, status),
                INDEX idx_channel (channel_id)
            )
            """
        )
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketCloseView())

    # -- Configuration ------------------------------------------------------

    async def _get_config(self, guild_id: int) -> dict | None:
        return await self.db.fetchone(
            "SELECT staff_role_id, category_id, log_channel_id "
            "FROM vibe_ticket_config WHERE guild_id = %s",
            (guild_id,),
        )

    async def _set_config_field(self, guild_id: int, field: str, value: int) -> None:
        # `field` is always an internal constant, never user input.
        await self.db.execute(
            f"INSERT INTO vibe_ticket_config (guild_id, {field}) VALUES (%s, %s) "
            f"ON DUPLICATE KEY UPDATE {field} = VALUES({field})",
            (guild_id, value),
        )

    @staticmethod
    def _missing_config(cfg: dict | None) -> list[str]:
        missing = []
        if not cfg or not cfg.get("staff_role_id"):
            missing.append("staff role (`$ticketstaff @role`)")
        if not cfg or not cfg.get("category_id"):
            missing.append("ticket category (`$ticketcategory <name>`)")
        if not cfg or not cfg.get("log_channel_id"):
            missing.append("log channel (`$ticketlog #channel`)")
        return missing

    @commands.command(name="ticketstaff")
    @admin_only()
    async def ticket_staff(self, ctx: commands.Context, role: discord.Role) -> None:
        """Set the staff role pinged in new tickets."""
        await self._set_config_field(ctx.guild.id, "staff_role_id", role.id)
        await ctx.send(embed=embeds.success(f"Staff role set to {role.mention}."))

    @commands.command(name="ticketcategory")
    @admin_only()
    async def ticket_category(
        self, ctx: commands.Context, *, category: discord.CategoryChannel
    ) -> None:
        """Set the category that new ticket channels are created under."""
        await self._set_config_field(ctx.guild.id, "category_id", category.id)
        await ctx.send(embed=embeds.success(f"Ticket category set to **{category.name}**."))

    @commands.command(name="ticketlog")
    @admin_only()
    async def ticket_log(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where closed-ticket transcripts are posted."""
        await self._set_config_field(ctx.guild.id, "log_channel_id", channel.id)
        await ctx.send(embed=embeds.success(f"Transcript log set to {channel.mention}."))

    @commands.command(name="ticketconfig")
    @admin_only()
    async def ticket_config(self, ctx: commands.Context) -> None:
        """Show the current ticket configuration for this server."""
        cfg = await self._get_config(ctx.guild.id)

        role = ctx.guild.get_role(cfg["staff_role_id"]) if cfg and cfg.get("staff_role_id") else None
        category = ctx.guild.get_channel(cfg["category_id"]) if cfg and cfg.get("category_id") else None
        log_channel = ctx.guild.get_channel(cfg["log_channel_id"]) if cfg and cfg.get("log_channel_id") else None

        lines = [
            f"**Staff role:** {role.mention if role else 'not set'}",
            f"**Category:** {category.name if category else 'not set'}",
            f"**Log channel:** {log_channel.mention if log_channel else 'not set'}",
        ]
        missing = self._missing_config(cfg)
        if missing:
            lines.append("")
            lines.append("Still needed: " + ", ".join(missing))
        else:
            lines.append("")
            lines.append("Ready. Run `$ticketpanel` to post the button.")

        await ctx.send(embed=embeds.info("Ticket configuration", "\n".join(lines)))

    @commands.command(name="ticketpanel")
    @admin_only()
    async def ticket_panel(self, ctx: commands.Context) -> None:
        """Post the "Open Ticket" panel in the current channel."""
        cfg = await self._get_config(ctx.guild.id)
        missing = self._missing_config(cfg)
        if missing:
            await ctx.send(
                embed=embeds.error(
                    "Finish setup before posting the panel. Still needed: "
                    + ", ".join(missing)
                )
            )
            return

        embed = embeds.info(
            "Need help?",
            "Click the button below to open a private ticket.\n"
            "Only you and the staff team will be able to see it.",
        )
        await ctx.send(embed=embed, view=TicketPanelView())

    # -- Opening ------------------------------------------------------------

    async def open_ticket(self, interaction: discord.Interaction) -> None:
        """Create a private ticket channel for the interacting member."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        opener = interaction.user

        cfg = await self._get_config(guild.id)
        if self._missing_config(cfg):
            await interaction.followup.send(
                "Tickets are not fully configured yet. Please tell an admin.",
                ephemeral=True,
            )
            return

        staff_role = guild.get_role(cfg["staff_role_id"])
        category = guild.get_channel(cfg["category_id"])
        if staff_role is None or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                "The configured staff role or category no longer exists. "
                "Please tell an admin to re-run setup.",
                ephemeral=True,
            )
            return

        existing = await self.db.fetchone(
            "SELECT channel_id FROM vibe_tickets "
            "WHERE guild_id = %s AND opener_id = %s AND status = 'open'",
            (guild.id, opener.id),
        )
        if existing:
            channel = guild.get_channel(existing["channel_id"])
            if channel is not None:
                await interaction.followup.send(
                    f"You already have an open ticket: {channel.mention}",
                    ephemeral=True,
                )
                return
            # The channel was deleted manually; mark the stale row closed.
            await self.db.execute(
                "UPDATE vibe_tickets SET status = 'closed' WHERE channel_id = %s",
                (existing["channel_id"],),
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            opener: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            staff_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            ),
        }

        safe_name = "".join(
            ch for ch in opener.name.lower() if ch.isalnum() or ch in "-_"
        ) or "member"

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{safe_name}"[:90],
                category=category,
                overwrites=overwrites,
                reason=f"Ticket opened by {opener} ({opener.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to create the ticket channel. "
                "Please tell an admin to check my Manage Channels permission.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            log.exception("Failed to create ticket channel in guild %s", guild.id)
            await interaction.followup.send(
                "Something went wrong creating the ticket channel. Please try again.",
                ephemeral=True,
            )
            return

        await self.db.execute(
            "INSERT INTO vibe_tickets (guild_id, channel_id, opener_id) "
            "VALUES (%s, %s, %s)",
            (guild.id, channel.id, opener.id),
        )

        embed = embeds.info(
            "Ticket opened",
            f"Opened by {opener.mention}.\n\n"
            "Describe what you need and staff will be with you soon.\n"
            "Press **Close Ticket** below when it's resolved.",
        )
        await channel.send(
            content=staff_role.mention, embed=embed, view=TicketCloseView()
        )
        await interaction.followup.send(
            f"Your ticket is open: {channel.mention}", ephemeral=True
        )

    # -- Closing ------------------------------------------------------------

    def _can_close(self, member: discord.Member, opener_id: int) -> bool:
        """The opener, mods, and admins may close a ticket."""
        return member.id == opener_id or is_mod(member) or is_admin(member)

    async def close_from_button(self, interaction: discord.Interaction) -> None:
        """Handle the in-ticket close button."""
        ticket = await self.db.fetchone(
            "SELECT opener_id FROM vibe_tickets "
            "WHERE channel_id = %s AND status = 'open'",
            (interaction.channel.id,),
        )
        if ticket is None:
            await interaction.response.send_message(
                "This is not an open ticket channel.", ephemeral=True
            )
            return

        if not self._can_close(interaction.user, ticket["opener_id"]):
            await interaction.response.send_message(
                "Only staff or the ticket opener can close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Closing ticket and saving transcript...")
        await self._finalize_close(interaction.channel, ticket["opener_id"], interaction.user)

    @commands.command(name="close")
    async def close_command(self, ctx: commands.Context) -> None:
        """Close the ticket in the current channel."""
        ticket = await self.db.fetchone(
            "SELECT opener_id FROM vibe_tickets "
            "WHERE channel_id = %s AND status = 'open'",
            (ctx.channel.id,),
        )
        if ticket is None:
            await ctx.send("This is not an open ticket channel.")
            return

        if not self._can_close(ctx.author, ticket["opener_id"]):
            await ctx.send("Only staff or the ticket opener can close this ticket.")
            return

        await ctx.send("Closing ticket and saving transcript...")
        await self._finalize_close(ctx.channel, ticket["opener_id"], ctx.author)

    async def _finalize_close(
        self,
        channel: discord.TextChannel,
        opener_id: int,
        closer: discord.abc.User,
    ) -> None:
        """Post the transcript, mark the ticket closed, and delete the channel."""
        cfg = await self._get_config(channel.guild.id)

        try:
            transcript = await self._build_transcript(channel)
            log_channel = (
                channel.guild.get_channel(cfg["log_channel_id"]) if cfg else None
            )
            if log_channel is not None:
                opener = channel.guild.get_member(opener_id)
                opener_label = f"{opener} ({opener_id})" if opener else f"user {opener_id}"
                file = discord.File(
                    io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{channel.name}-transcript.txt",
                )
                embed = embeds.info(
                    "Ticket closed",
                    f"**Ticket:** #{channel.name}\n"
                    f"**Opened by:** {opener_label}\n"
                    f"**Closed by:** {closer} ({closer.id})",
                )
                await log_channel.send(embed=embed, file=file)
        except Exception:
            log.exception("Failed to post transcript for channel %s", channel.id)

        await self.db.execute(
            "UPDATE vibe_tickets SET status = 'closed', "
            "closed_at = CURRENT_TIMESTAMP, closed_by = %s "
            "WHERE channel_id = %s",
            (closer.id, channel.id),
        )

        try:
            await channel.send(
                f"Ticket closed. This channel will be deleted in {DELETE_DELAY_SECONDS} seconds."
            )
        except discord.HTTPException:
            pass

        await asyncio.sleep(DELETE_DELAY_SECONDS)
        try:
            await channel.delete(reason=f"Ticket closed by {closer} ({closer.id})")
        except discord.HTTPException:
            log.exception("Failed to delete ticket channel %s", channel.id)

    @staticmethod
    async def _build_transcript(channel: discord.TextChannel) -> str:
        """Render the channel history as a plain-text transcript."""
        lines = [
            f"Transcript: #{channel.name}",
            f"Server: {channel.guild.name} ({channel.guild.id})",
            f"Generated: {discord.utils.utcnow():%Y-%m-%d %H:%M} UTC",
            "-" * 50,
        ]
        async for message in channel.history(
            limit=TRANSCRIPT_MESSAGE_LIMIT, oldest_first=True
        ):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{timestamp}] {message.author}: {message.content or ''}")
            for attachment in message.attachments:
                lines.append(f"    [attachment] {attachment.url}")
        return "\n".join(lines)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
