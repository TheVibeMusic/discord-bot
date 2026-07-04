"""Server resources list.

`/resources` shows a blue embed of clickable links (website, socials, etc.).
Admins manage the list with $addresource, $deleteresource, and
$replaceresource. The table is seeded with the server's default links the
first time the cog loads.
"""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import embeds
from utils.permissions import admin_only

# Accepts either: $addresource [Name](https://url)  or  $addresource Name https://url
MARKDOWN_LINK = re.compile(r"^\[(?P<name>.+)\]\((?P<url>https?://\S+)\)$")

DEFAULT_RESOURCES = [
    ("WEBSITE | Vibe Music", "https://vibe-music-dzuo.vercel.app/"),
    ("TWITCH", "https://www.twitch.tv/theaiumbrella"),
    ("REDDIT", "https://www.reddit.com/r/ViibrMusic/"),
    ("YOUTUBE", "https://www.youtube.com/@viibrmusic"),
]


def parse_resource(text: str) -> tuple[str, str] | None:
    """Parse '[Name](url)' or 'Name url' into (name, url), or None."""
    text = text.strip()
    match = MARKDOWN_LINK.match(text)
    if match:
        return match.group("name").strip(), match.group("url").strip()
    if " " in text:
        name, _, url = text.rpartition(" ")
        if url.startswith(("http://", "https://")):
            return name.strip(), url
    return None


class Resources(commands.Cog):
    """Clickable server links, managed by admins."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_resources (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                name VARCHAR(100) NOT NULL,
                url VARCHAR(500) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_resource (guild_id, name)
            )
            """
        )
        # Seed the defaults for the home server once.
        if config.GUILD_ID:
            row = await self.db.fetchone(
                "SELECT COUNT(*) AS c FROM vibe_resources WHERE guild_id = %s",
                (config.GUILD_ID,),
            )
            if row and row["c"] == 0:
                for name, url in DEFAULT_RESOURCES:
                    await self.db.execute(
                        "INSERT INTO vibe_resources (guild_id, name, url) "
                        "VALUES (%s, %s, %s)",
                        (config.GUILD_ID, name, url),
                    )

    # -- Member slash command ---------------------------------------------------

    @app_commands.command(name="resources", description="Server links: website, socials, and more")
    async def resources(self, interaction: discord.Interaction) -> None:
        rows = await self.db.fetchall(
            "SELECT name, url FROM vibe_resources WHERE guild_id = %s ORDER BY id",
            (interaction.guild_id,),
        )
        if not rows:
            await interaction.response.send_message(
                "No resources have been added yet.", ephemeral=True
            )
            return

        lines = [f"[{row['name']}]({row['url']})" for row in rows]
        embed = embeds.info("Server Resources", "\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- Admin commands -----------------------------------------------------------

    @commands.command(name="addresource")
    @admin_only()
    async def add_resource(self, ctx: commands.Context, *, entry: str) -> None:
        """Add a link: $addresource [Name](url) or $addresource Name url."""
        parsed = parse_resource(entry)
        if parsed is None:
            await ctx.send(
                embed=embeds.error("Format: `$addresource [Name](https://url)` or `$addresource Name https://url`")
            )
            return
        name, url = parsed
        await self.db.execute(
            "INSERT INTO vibe_resources (guild_id, name, url) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE url = VALUES(url)",
            (ctx.guild.id, name, url),
        )
        await ctx.send(embed=embeds.success(f"Added **{name}** to /resources."))

    @commands.command(name="deleteresource")
    @admin_only()
    async def delete_resource(self, ctx: commands.Context, *, name: str) -> None:
        """Remove a link by name: $deleteresource Name."""
        name = name.strip().strip("[]")
        removed = await self.db.execute(
            "DELETE FROM vibe_resources WHERE guild_id = %s AND name = %s",
            (ctx.guild.id, name),
        )
        if removed:
            await ctx.send(embed=embeds.success(f"Removed **{name}** from /resources."))
        else:
            await ctx.send(embed=embeds.error(f"No resource named **{name}** found."))

    @commands.command(name="replaceresource")
    @admin_only()
    async def replace_resource(self, ctx: commands.Context, *, entry: str) -> None:
        """Replace a link's URL: $replaceresource [Name](new-url)."""
        parsed = parse_resource(entry)
        if parsed is None:
            await ctx.send(
                embed=embeds.error("Format: `$replaceresource [Name](https://new-url)`")
            )
            return
        name, url = parsed
        updated = await self.db.execute(
            "UPDATE vibe_resources SET url = %s WHERE guild_id = %s AND name = %s",
            (url, ctx.guild.id, name),
        )
        if updated:
            await ctx.send(embed=embeds.success(f"Updated the link for **{name}**."))
        else:
            await ctx.send(embed=embeds.error(f"No resource named **{name}** found."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Resources(bot))
