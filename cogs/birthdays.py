"""Birthday tracker.

Members register their own birthday with /addmybd. A daily task announces the
day's birthdays in the configured channel, sending the premade greeting card
image and one of the birthday songs from the assets folder (rotating by day),
plus a button members can press to send wishes.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import re
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymysql.err import IntegrityError

from utils import embeds
from utils.permissions import admin_only

log = logging.getLogger("vibe.birthdays")

WISH_BUTTON_ID = "viibr_bday_wish"

# Announcement time: 14:00 UTC (9 AM Central / 10 AM Eastern).
ANNOUNCE_TIME = datetime.time(hour=14, minute=0, tzinfo=datetime.timezone.utc)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "ASSETS" / "birthday tracker"
CARD_IMAGE = ASSETS_DIR / "birthday-message.png"  # fallback when no cards are configured
SONGS_DIR = ASSETS_DIR / "mp3s"

# Admins add extra greeting cards by URL ($bdaycard). Host the image somewhere
# with a stable direct link — Imgur (i.imgur.com/...png) is a good bet, or any
# image already uploaded to Discord. Cards rotate randomly per birthday; if
# none are configured the built-in card image is used.
CARD_URL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# Community song submissions.
MAX_SONG_SUBMISSIONS = 25

# Downloaded community mp3s live outside the repo (server-local storage).
COMMUNITY_SONGS_DIR = Path(__file__).resolve().parent.parent / "community_songs"

SUNO_PAGE_RE = re.compile(r"https?://(?:www\.)?suno\.com/(?:s|song)/\S+", re.I)
SUNO_CDN_RE = re.compile(r"https?://cdn\d*\.suno(?:\.ai|\.com)/[A-Za-z0-9\-]+\.mp3")
MAX_DOWNLOAD_BYTES = 24_000_000  # stay under Discord's attachment limit

SONG_RULES = (
    "**Want your song played on someone's birthday?** Submit a birthday song "
    "you made and it joins the rotation!\n\n"
    "**The rules:**\n"
    "\N{BULLET} It has to be a birthday song you made yourself\n"
    "\N{BULLET} Nothing gross, nothing R-rated\n"
    "\N{BULLET} Keep it positive and fun for everyone\n"
    "\N{BULLET} One song per member — submitting again replaces your old one\n\n"
    "Staff may remove songs that break the rules."
)

SONGS_FULL_MESSAGE = (
    "The birthday song list is full right now — thank you all for so much "
    "birthday spirit! Spots open up when the rotation gets refreshed, so "
    "check back later."
)


class BirthdayWishView(discord.ui.View):
    """Persistent view holding the 'Send birthday wishes' button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Send birthday wishes",
        style=discord.ButtonStyle.primary,
        emoji="\N{BIRTHDAY CAKE}",
        custom_id=WISH_BUTTON_ID,
    )
    async def send_wishes(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        cog = interaction.client.get_cog("Birthdays")
        if cog is None:
            await interaction.response.send_message(
                "Birthdays are unavailable right now.", ephemeral=True
            )
            return
        await cog.record_wish(interaction)


class SongSubmitModal(discord.ui.Modal, title="Submit your birthday song"):
    """Modal collecting a song title and link from a member."""

    song_title = discord.ui.TextInput(
        label="Song title",
        placeholder="My Amazing Birthday Banger",
        max_length=100,
    )
    song_url = discord.ui.TextInput(
        label="Song link",
        placeholder="https://suno.com/s/... or a YouTube link",
        max_length=400,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Birthdays")
        if cog is None:
            await interaction.response.send_message(
                "Birthdays are unavailable right now.", ephemeral=True
            )
            return
        await cog.save_song_submission(
            interaction, str(self.song_title), str(self.song_url)
        )


class SongMenuView(discord.ui.View):
    """Menu view with the submit button, shown by /birthdaysong."""

    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(
        label="Submit a song",
        style=discord.ButtonStyle.primary,
        emoji="\N{MUSICAL NOTE}",
    )
    async def submit_song(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        cog = interaction.client.get_cog("Birthdays")
        if cog is None:
            await interaction.response.send_message(
                "Birthdays are unavailable right now.", ephemeral=True
            )
            return
        if await cog.submissions_full(interaction.guild_id, interaction.user.id):
            await interaction.response.send_message(
                SONGS_FULL_MESSAGE, ephemeral=True
            )
            return
        await interaction.response.send_modal(SongSubmitModal())


class Birthdays(commands.Cog):
    """Self-service birthday registration and daily announcements."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_birthdays (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NULL,
                name VARCHAR(100) NULL,
                month INT NOT NULL,
                day INT NOT NULL,
                song_url VARCHAR(500) NULL,
                personal_message VARCHAR(500) NULL,
                added_by BIGINT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_member (guild_id, user_id),
                INDEX idx_date (guild_id, month, day)
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_bday_config (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT NULL,
                image_url VARCHAR(500) NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_bday_wishes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_wish (message_id, user_id)
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_bday_cards (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                url VARCHAR(500) NOT NULL,
                added_by BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_guild (guild_id)
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_bday_submissions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                title VARCHAR(100) NOT NULL,
                url VARCHAR(400) NOT NULL,
                local_file VARCHAR(200) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_submitter (guild_id, user_id)
            )
            """
        )

        self.bot.add_view(BirthdayWishView())
        if not self.announce_birthdays.is_running():
            self.announce_birthdays.start()

    async def cog_unload(self) -> None:
        self.announce_birthdays.cancel()

    # -- Member slash commands -------------------------------------------------

    @app_commands.command(name="addmybd", description="Add your birthday to the server calendar")
    @app_commands.describe(month="Month (1-12)", day="Day (1-31)")
    async def add_my_birthday(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12],
        day: app_commands.Range[int, 1, 31],
    ) -> None:
        await self.db.execute(
            "INSERT INTO vibe_birthdays (guild_id, user_id, month, day) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE month = VALUES(month), day = VALUES(day)",
            (interaction.guild_id, interaction.user.id, month, day),
        )
        await interaction.response.send_message(
            f"Your birthday is saved as **{month:02d}/{day:02d}**. "
            "Use /removemybd if you change your mind.",
            ephemeral=True,
        )

    @app_commands.command(name="removemybd", description="Remove your birthday from the calendar")
    async def remove_my_birthday(self, interaction: discord.Interaction) -> None:
        removed = await self.db.execute(
            "DELETE FROM vibe_birthdays WHERE guild_id = %s AND user_id = %s",
            (interaction.guild_id, interaction.user.id),
        )
        text = (
            "Your birthday has been removed from the calendar."
            if removed
            else "You don't have a birthday on the calendar."
        )
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="calendar", description="See upcoming birthdays")
    async def calendar(self, interaction: discord.Interaction) -> None:
        rows = await self.db.fetchall(
            "SELECT user_id, name, month, day FROM vibe_birthdays WHERE guild_id = %s",
            (interaction.guild_id,),
        )
        if not rows:
            await interaction.response.send_message(
                "No birthdays on the calendar yet. Add yours with /addmybd!",
                ephemeral=True,
            )
            return

        today = datetime.date.today()

        def days_until(row: dict) -> int:
            for year in (today.year, today.year + 1):
                try:
                    candidate = datetime.date(year, row["month"], row["day"])
                except ValueError:  # Feb 29 in a non-leap year
                    continue
                if candidate >= today:
                    return (candidate - today).days
            return 400

        upcoming = sorted(rows, key=days_until)[:10]
        lines = []
        for row in upcoming:
            who = f"<@{row['user_id']}>" if row["user_id"] else f"**{row['name']}**"
            when = f"{row['month']:02d}/{row['day']:02d}"
            days = days_until(row)
            note = "today!" if days == 0 else f"in {days} day(s)"
            lines.append(f"{when} — {who} ({note})")

        embed = embeds.info("Upcoming birthdays", "\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- Community song submissions ----------------------------------------------

    @app_commands.command(
        name="birthdaysong",
        description="Submit a birthday song you made to the birthday rotation",
    )
    async def birthday_song(self, interaction: discord.Interaction) -> None:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM vibe_bday_submissions WHERE guild_id = %s",
            (interaction.guild_id,),
        )
        used = row["c"] if row else 0
        embed = embeds.info("Community Birthday Songs", SONG_RULES)
        embed.set_footer(text=f"{used}/{MAX_SONG_SUBMISSIONS} rotation spots filled")
        await interaction.response.send_message(
            embed=embed, view=SongMenuView(), ephemeral=True
        )

    async def submissions_full(self, guild_id: int, user_id: int) -> bool:
        """True if the list is at cap and this member isn't replacing their own."""
        existing = await self.db.fetchone(
            "SELECT 1 FROM vibe_bday_submissions "
            "WHERE guild_id = %s AND user_id = %s",
            (guild_id, user_id),
        )
        if existing:
            return False  # replacing their own song is always allowed
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM vibe_bday_submissions WHERE guild_id = %s",
            (guild_id,),
        )
        return bool(row and row["c"] >= MAX_SONG_SUBMISSIONS)

    async def save_song_submission(
        self, interaction: discord.Interaction, title: str, url: str
    ) -> None:
        """Validate and store a member's song from the modal."""
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            await interaction.response.send_message(
                "That link doesn't look right — it should start with https://",
                ephemeral=True,
            )
            return
        if await self.submissions_full(interaction.guild_id, interaction.user.id):
            await interaction.response.send_message(SONGS_FULL_MESSAGE, ephemeral=True)
            return

        replacing = await self.db.fetchone(
            "SELECT local_file FROM vibe_bday_submissions "
            "WHERE guild_id = %s AND user_id = %s",
            (interaction.guild_id, interaction.user.id),
        )
        await self.db.execute(
            "INSERT INTO vibe_bday_submissions (guild_id, user_id, title, url) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE title = VALUES(title), url = VALUES(url), "
            "local_file = NULL",
            (interaction.guild_id, interaction.user.id, title.strip(), url),
        )

        # A replaced submission's old download is stale — remove it.
        if replacing and replacing["local_file"]:
            old = COMMUNITY_SONGS_DIR / replacing["local_file"]
            old.unlink(missing_ok=True)

        # Suno links: fetch the actual mp3 in the background so birthdays can
        # attach the file instead of a link. Fails quietly to link-fallback.
        if SUNO_PAGE_RE.match(url):
            asyncio.create_task(
                self._download_suno_mp3(interaction.guild_id, interaction.user.id, url)
            )

        # Public so the community sees new submissions land — it's the whole
        # point of the feature being an engagement thing.
        text = (
            f"{interaction.user.mention} updated their birthday song to "
            f"**{title.strip()}** — it's in the rotation! \N{MUSICAL NOTE}"
            if replacing
            else f"{interaction.user.mention} added **{title.strip()}** to the "
            "birthday rotation — they'll get credit when it plays. "
            "\N{MUSICAL NOTE}"
        )
        await interaction.response.send_message(text)

    async def _download_suno_mp3(self, guild_id: int, user_id: int, url: str) -> None:
        """Fetch the mp3 behind a Suno song link and store it locally.

        On success the submission row is updated with the file name; on any
        failure the row keeps local_file NULL and birthdays fall back to
        posting the link.
        """
        filename = f"{guild_id}_{user_id}.mp3"
        dest = COMMUNITY_SONGS_DIR / filename
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return
                    page = await resp.text()
                match = SUNO_CDN_RE.search(page)
                if match is None:
                    log.info("No Suno CDN mp3 found on page for %s", url)
                    return
                async with session.get(match.group(0)) as resp:
                    if resp.status != 200:
                        return
                    if int(resp.headers.get("Content-Length") or 0) > MAX_DOWNLOAD_BYTES:
                        log.info("Suno mp3 too large to attach for %s", url)
                        return
                    data = await resp.read()
            if len(data) > MAX_DOWNLOAD_BYTES:
                return
            COMMUNITY_SONGS_DIR.mkdir(exist_ok=True)
            dest.write_bytes(data)
            await self.db.execute(
                "UPDATE vibe_bday_submissions SET local_file = %s "
                "WHERE guild_id = %s AND user_id = %s",
                (filename, guild_id, user_id),
            )
        except Exception:
            log.exception("Failed to download Suno mp3 for %s", url)

    @commands.command(name="bdsongs")
    @admin_only()
    async def list_bd_songs(self, ctx: commands.Context) -> None:
        """List community song submissions with their ids. (Admin)"""
        rows = await self.db.fetchall(
            "SELECT id, user_id, title, url FROM vibe_bday_submissions "
            "WHERE guild_id = %s ORDER BY id",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=embeds.info("Community songs", "No submissions yet."))
            return
        lines = [
            f"`#{row['id']}` [{row['title']}]({row['url']}) — <@{row['user_id']}>"
            for row in rows
        ]
        embed = embeds.info(
            f"Community songs ({len(rows)}/{MAX_SONG_SUBMISSIONS})", "\n".join(lines)
        )
        await ctx.send(embed=embed)

    @commands.command(name="removebdsong")
    @admin_only()
    async def remove_bd_song(self, ctx: commands.Context, submission_id: int) -> None:
        """Remove a community song by its id from $bdsongs. (Admin)"""
        removed = await self.db.execute(
            "DELETE FROM vibe_bday_submissions WHERE guild_id = %s AND id = %s",
            (ctx.guild.id, submission_id),
        )
        if removed:
            await ctx.send(embed=embeds.success(f"Removed submission #{submission_id}."))
        else:
            await ctx.send(embed=embeds.error(f"No submission #{submission_id} found."))

    # -- Admin command -----------------------------------------------------------

    @commands.command(name="bdaychannel")
    @admin_only()
    async def bday_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel where birthday announcements are posted."""
        await self.db.execute(
            "INSERT INTO vibe_bday_config (guild_id, channel_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id)",
            (ctx.guild.id, channel.id),
        )
        await ctx.send(
            embed=embeds.success(f"Birthday announcements will post in {channel.mention}.")
        )

    @commands.command(name="bdaycard")
    @admin_only()
    async def add_bday_card(self, ctx: commands.Context, url: str) -> None:
        """Add a greeting card image to the birthday rotation, by URL."""
        url = url.strip("<>")
        if not url.startswith(("http://", "https://")):
            await ctx.send(
                embed=embeds.error(
                    "That doesn't look like a link. Upload the card to Imgur "
                    "(or any channel here) and paste the direct image URL — "
                    "it should end in .png, .jpg, or .gif."
                )
            )
            return
        if not url.lower().split("?")[0].endswith(CARD_URL_SUFFIXES):
            await ctx.send(
                embed=embeds.error(
                    "That link doesn't point straight at an image. On Imgur, "
                    "right-click the image and copy the *image* address — it "
                    "looks like `https://i.imgur.com/abc123.png`, not "
                    "`https://imgur.com/gallery/...`."
                )
            )
            return

        await self.db.execute(
            "INSERT INTO vibe_bday_cards (guild_id, url, added_by) VALUES (%s, %s, %s)",
            (ctx.guild.id, url, ctx.author.id),
        )
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM vibe_bday_cards WHERE guild_id = %s",
            (ctx.guild.id,),
        )
        count = row["c"] if row else 1
        embed = embeds.success(
            f"Card added — **{count}** in the rotation. One is picked at random "
            "for each birthday."
        )
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @commands.command(name="bdaycards")
    @admin_only()
    async def list_bday_cards(self, ctx: commands.Context) -> None:
        """List the birthday cards in the rotation, with their ids."""
        rows = await self.db.fetchall(
            "SELECT id, url, added_by FROM vibe_bday_cards WHERE guild_id = %s ORDER BY id",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(
                embed=embeds.info(
                    "Birthday cards",
                    "No cards added yet — the built-in card is used for every "
                    "birthday. Add more with `$bdaycard <image url>`.",
                )
            )
            return
        lines = [
            f"`#{row['id']}` [card]({row['url']}) — added by <@{row['added_by']}>"
            for row in rows
        ]
        await ctx.send(
            embed=embeds.info(f"Birthday cards ({len(rows)})", "\n".join(lines))
        )

    @commands.command(name="removebdcard")
    @admin_only()
    async def remove_bday_card(self, ctx: commands.Context, card_id: int) -> None:
        """Remove a birthday card from the rotation by its id."""
        removed = await self.db.execute(
            "DELETE FROM vibe_bday_cards WHERE guild_id = %s AND id = %s",
            (ctx.guild.id, card_id),
        )
        if removed:
            await ctx.send(embed=embeds.success(f"Removed card #{card_id}."))
        else:
            await ctx.send(embed=embeds.error(f"No card #{card_id} found."))

    # -- Wishes button -----------------------------------------------------------

    async def record_wish(self, interaction: discord.Interaction) -> None:
        """Record one wish per member per announcement and update the count."""
        try:
            await self.db.execute(
                "INSERT INTO vibe_bday_wishes (message_id, user_id) VALUES (%s, %s)",
                (interaction.message.id, interaction.user.id),
            )
        except IntegrityError:
            await interaction.response.send_message(
                "You've already sent your birthday wishes for this one!",
                ephemeral=True,
            )
            return

        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM vibe_bday_wishes WHERE message_id = %s",
            (interaction.message.id,),
        )
        count = row["c"] if row else 1

        try:
            await interaction.message.edit(
                content=f"{interaction.message.content.splitlines()[0]}\n"
                f"\N{PARTY POPPER} **{count}** birthday wish(es) from the community!"
            )
        except (discord.HTTPException, IndexError):
            pass

        await interaction.response.send_message(
            "Your birthday wishes have been added. \N{BIRTHDAY CAKE}", ephemeral=True
        )

    # -- Daily announcement task ---------------------------------------------------

    @tasks.loop(time=ANNOUNCE_TIME)
    async def announce_birthdays(self) -> None:
        today = datetime.date.today()
        configs = await self.db.fetchall(
            "SELECT guild_id, channel_id FROM vibe_bday_config "
            "WHERE channel_id IS NOT NULL"
        )
        for config_row in configs:
            guild = self.bot.get_guild(config_row["guild_id"])
            if guild is None:
                continue
            channel = guild.get_channel(config_row["channel_id"])
            if not isinstance(channel, discord.TextChannel):
                continue

            rows = await self.db.fetchall(
                "SELECT * FROM vibe_birthdays "
                "WHERE guild_id = %s AND month = %s AND day = %s",
                (guild.id, today.month, today.day),
            )
            for row in rows:
                await self._send_greeting(channel, row, today)

    async def _send_greeting(
        self, channel: discord.TextChannel, row: dict, today: datetime.date
    ) -> None:
        """Send a greeting card and the day's song for one birthday.

        Cards added with $bdaycard rotate at random; with none configured the
        built-in card image is attached instead. The song rotation is the local
        mp3 assets plus community submissions, indexed by day of year — local
        picks are attached as files, community picks are posted as links with
        credit to the member who made them.
        """
        who = f"<@{row['user_id']}>" if row["user_id"] else f"**{row['name']}**"
        content = f"\N{BIRTHDAY CAKE} Happy Birthday {who}!"

        files = []
        embed = None
        cards = await self.db.fetchall(
            "SELECT url FROM vibe_bday_cards WHERE guild_id = %s",
            (channel.guild.id,),
        )
        if cards:
            embed = discord.Embed(color=embeds.BLUE)
            embed.set_image(url=random.choice(cards)["url"])
        elif CARD_IMAGE.is_file():
            files.append(discord.File(CARD_IMAGE))
        else:
            log.warning("Birthday card image not found at %s", CARD_IMAGE)

        local_songs = sorted(SONGS_DIR.glob("*.mp3"))
        submissions = await self.db.fetchall(
            "SELECT user_id, title, url, local_file FROM vibe_bday_submissions "
            "WHERE guild_id = %s ORDER BY id",
            (channel.guild.id,),
        )
        pool_size = len(local_songs) + len(submissions)
        if pool_size:
            index = today.timetuple().tm_yday % pool_size
            if index < len(local_songs):
                files.append(discord.File(local_songs[index]))
            else:
                pick = submissions[index - len(local_songs)]
                credit = (
                    f"\nToday's birthday song: **{pick['title']}** — "
                    f"written by <@{pick['user_id']}>"
                )
                downloaded = (
                    COMMUNITY_SONGS_DIR / pick["local_file"]
                    if pick["local_file"]
                    else None
                )
                if downloaded is not None and downloaded.is_file():
                    content += credit
                    files.append(discord.File(downloaded))
                else:
                    content += f"{credit}\n{pick['url']}"

        try:
            await channel.send(
                content=content,
                embed=embed,
                files=files,
                view=BirthdayWishView(),
            )
        except discord.HTTPException:
            log.exception("Failed to send birthday greeting in %s", channel.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Birthdays(bot))
