"""Entry point for the Viibr bot."""

from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import config
from utils.database import Database

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("vibe")

INITIAL_EXTENSIONS = [
    "cogs.general",
    "cogs.help",
    "cogs.tickets",
    "cogs.moderation",
    "cogs.counting",
    "cogs.birthdays",
    "cogs.resources",
]


def allowed_channel(interaction: discord.Interaction) -> bool:
    """Whether this interaction may run where it was invoked.

    Slash commands are confined to COMMAND_CHANNEL_IDS so they don't clutter
    conversation channels. Threads inherit their parent channel's permission,
    and the exempt users may run commands anywhere. With no channels
    configured the restriction is off, so a missing environment value can
    never make the bot look broken server-wide.
    """
    if not config.COMMAND_CHANNEL_IDS:
        return True
    if interaction.user.id in config.COMMAND_EXEMPT_IDS:
        return True
    if interaction.channel_id in config.COMMAND_CHANNEL_IDS:
        return True
    parent_id = getattr(interaction.channel, "parent_id", None)
    return parent_id in config.COMMAND_CHANNEL_IDS


class VibeCommandTree(app_commands.CommandTree):
    """Command tree that keeps slash commands in their designated channels."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if allowed_channel(interaction):
            return True

        where = ", ".join(f"<#{cid}>" for cid in sorted(config.COMMAND_CHANNEL_IDS))
        await interaction.response.send_message(
            f"Bot commands are used in {where}. Head over there and try again.",
            ephemeral=True,
        )
        return False

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Tell the user a slash command failed, and report it to staff.

        The default only writes a log line, so the member is left staring at
        "The application did not respond" while nothing reaches the error
        channel. This mirrors what on_command_error does for $ commands.
        """
        # A failed interaction_check already told the user where to go; logging
        # it as an unhandled error would just be noise.
        if isinstance(error, app_commands.CheckFailure):
            return

        original = getattr(error, "original", error)

        # The interaction may already be answered or deferred, which decides
        # whether a reply goes through response or followup. Either can fail on
        # its own (a 3-second interaction can expire), so this never raises out
        # of the error handler.
        try:
            notice = "Something went wrong running that — staff have been notified."
            if interaction.response.is_done():
                await interaction.followup.send(notice, ephemeral=True)
            else:
                await interaction.response.send_message(notice, ephemeral=True)
        except discord.HTTPException:
            log.warning("Could not tell the user their slash command failed")

        command = interaction.command.qualified_name if interaction.command else "?"
        await self.client.report_error(
            f"`/{command}` failed in #{interaction.channel}",
            f"{type(original).__name__}: {original}",
            guild=interaction.guild,
            error=original,
        )


class VibeBot(commands.Bot):
    """The Viibr bot client."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=config.PREFIX,
            intents=intents,
            help_command=None,
            tree_cls=VibeCommandTree,
            # Nothing this bot sends should ever ping @everyone or a role by
            # accident. Member-supplied text reaches messages in several
            # features, and per-call allowed_mentions is easy to forget, so the
            # safe setting is the default and the rare exception opts in
            # (see utils.mentions.only_ping_role).
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True
            ),
        )
        self.db = Database()

    async def setup_hook(self) -> None:
        await self.db.connect()
        for extension in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(extension)
                log.info("Loaded extension %s", extension)
            except Exception:
                log.exception("Failed to load extension %s", extension)
        synced = await self.tree.sync()
        log.info("Synced %d application command(s)", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, getattr(self.user, "id", "?"))

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """Say something when a prefix command fails.

        Without this, discord.py logs the failure and the channel sees nothing
        at all, so a permission problem, a missing argument and an outright
        crash are indistinguishable from the bot ignoring you.
        """
        error = getattr(error, "original", error)

        if isinstance(error, commands.CommandNotFound):
            return  # someone typed $whatever; staying quiet is right

        if isinstance(error, commands.CheckFailure):
            await ctx.send("That command is staff only.")
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"`{config.PREFIX}{ctx.command}` needs another value — "
                f"`{ctx.command.signature}`."
            )
            return

        if isinstance(error, (commands.BadArgument, commands.ArgumentParsingError)):
            await ctx.send(
                f"That doesn't look right for `{config.PREFIX}{ctx.command}` — "
                f"expected `{ctx.command.signature}`."
            )
            return

        # Anything else is a bug. Tell the channel briefly, and send the
        # details somewhere staff will actually see them.
        await ctx.send("Something went wrong running that — staff have been notified.")
        await self.report_error(
            f"`{config.PREFIX}{ctx.command}` failed in #{ctx.channel}",
            f"{type(error).__name__}: {error}",
            guild=ctx.guild,
            error=error,
        )

    async def report_error(
        self,
        context: str,
        detail: str = "",
        guild: discord.Guild | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Post an error report to the error channel.

        Uses the guild's configured channel ($errorchannel) when one is set,
        falling back to the ERROR_CHANNEL_ID environment default. Logs only if
        neither resolves — error reporting must never itself raise.
        """
        # exc_info keeps the stack trace in the log; without it an overridden
        # error handler loses the traceback discord.py would have printed.
        log.error("%s %s", context, detail, exc_info=error)

        channel = None
        if guild is not None:
            try:
                row = await self.db.fetchone(
                    "SELECT error_channel_id FROM vibe_mod_config WHERE guild_id = %s",
                    (guild.id,),
                )
                if row and row["error_channel_id"]:
                    channel = guild.get_channel(row["error_channel_id"])
            except Exception:
                log.exception("Failed to look up the error channel")
        if channel is None:
            channel = self.get_channel(config.ERROR_CHANNEL_ID)
        if channel is None:
            return

        text = f"\N{WARNING SIGN} **Bot error:** {context}"
        if detail:
            text += f"\n```\n{detail[:1500]}\n```"
        try:
            await channel.send(text)
        except discord.HTTPException:
            log.exception("Failed to deliver error report")

    async def close(self) -> None:
        await self.db.close()
        await super().close()


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    VibeBot().run(token)


if __name__ == "__main__":
    main()
