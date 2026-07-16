"""Entry point for the Viibr bot."""

from __future__ import annotations

import logging
import os

import discord
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

    async def report_error(
        self,
        context: str,
        detail: str = "",
        guild: discord.Guild | None = None,
    ) -> None:
        """Post an error report to the error channel.

        Uses the guild's configured channel ($errorchannel) when one is set,
        falling back to the ERROR_CHANNEL_ID environment default. Logs only if
        neither resolves — error reporting must never itself raise.
        """
        log.error("%s %s", context, detail)

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
