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
