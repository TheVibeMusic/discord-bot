"""Counting game.

Members count upward in a designated channel, one number per message. Correct
counts get a lightning-bolt reaction. The same member cannot count twice in a
row. Non-numeric messages are ignored, so normal chat is allowed.

Modes:
- hard: a wrong number resets the count to zero.
- easy: a wrong number is marked with an X but the count stands.

Milestones (100, 200, 500, 1000, then every 1000) get a celebration message;
admins can attach one or more images/gifs to a specific number, and the bot
rotates through them at random so a repeat milestone always feels fresh.

Every correct count and every miss also feeds a per-member leaderboard,
visible to everyone with /countboard. A miscount offers a one-shot "Double or
Nothing" gamble, and every reset drops a random stat or trivia line.
"""

from __future__ import annotations

import logging
import random
import re

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import embeds
from utils.permissions import admin_only

log = logging.getLogger("vibe.counting")

VERIFY_EMOJI = "\N{HIGH VOLTAGE SIGN}"  # ⚡
MISS_EMOJI = "\N{CROSS MARK}"  # ❌

# Leaderboard scoring. Correct counts build points, landing exactly on a
# milestone pays a bonus, and misses cost enough to sting without wiping a
# careful counter out (a miss undoes ten correct counts).
POINTS_CORRECT = 1
POINTS_MILESTONE_BONUS = 5
POINTS_MISS = -10

LEADERBOARD_SIZE = 10

# How long the "Double or Nothing" button stays live after a miss.
GAMBLE_WINDOW_SECONDS = 25

FIXED_MILESTONES = {100, 200, 500}

NUMBER_RE = re.compile(r"-?\d+")

# Random chaos dropped into reset messages alongside the real stats, so a
# wipe always comes with something to read. Kept text-only and low-key to
# match the rest of the bot's voice.
TRIVIA_FACTS = (
    "Fun fact: zero has no symbol of its own in Roman numerals.",
    "Fun fact: a group of flamingos is called a flamboyance.",
    "Fun fact: honey never spoils — 3,000-year-old jars are still edible.",
    "Fun fact: the shortest war in history lasted about 38 minutes.",
    "Fun fact: octopuses have three hearts and blue blood.",
    "Fun fact: bananas are berries, but strawberries are not.",
    "Fun fact: there are more possible chess games than atoms in the observable universe.",
    "Fun fact: a day on Venus is longer than its year.",
    "Fun fact: 111,111,111 x 111,111,111 = 12,345,678,987,654,321.",
    "Fun fact: the number four is the only one whose letters equal its value.",
)


def is_milestone(number: int) -> bool:
    return number in FIXED_MILESTONES or (number >= 1000 and number % 1000 == 0)


# ◸──────── ✧ ────────🔹-🎲-🔹 ──────── ◇ ———————◹
#       SECTION: Double or Nothing — redemption gamble after a miss
# ◺──────── ✧ ────────🔹-🎲-🔹 ──────── ◇ ———————◿
class GambleView(discord.ui.View):
    """One-shot "Double or Nothing" offered to whoever just miscounted.

    Pressing it flips a coin. Win and the miss is undone: the point penalty is
    refunded, the miss is struck from their record, and in hard mode the count
    is restored to where it was — as long as nobody has started a fresh count
    in the meantime. Lose and the penalty doubles. Ignore it and the normal
    miss penalty simply stands.

    Only the member who missed can press the button, and it resolves once.
    """

    def __init__(
        self,
        cog: "Counting",
        *,
        misser_id: int,
        guild_id: int,
        restore_count: int | None,
        restore_last_user: int | None,
    ) -> None:
        super().__init__(timeout=GAMBLE_WINDOW_SECONDS)
        self.cog = cog
        self.misser_id = misser_id
        self.guild_id = guild_id
        # None in easy mode (nothing was reset); the pre-miss count in hard mode.
        self.restore_count = restore_count
        self.restore_last_user = restore_last_user
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.misser_id:
            await interaction.response.send_message(
                "This gamble belongs to whoever just missed — not you!",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Double or Nothing", style=discord.ButtonStyle.danger)
    async def gamble(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        button.disabled = True
        penalty = abs(POINTS_MISS)

        if random.random() < 0.5:
            # Win: refund the points, strike the miss, try to undo the reset.
            await self.cog.record_stat(
                self.guild_id, self.misser_id, points=penalty, miss=-1
            )
            restored = await self.cog.restore_after_gamble(
                self.guild_id, self.restore_count, self.restore_last_user
            )
            if restored:
                text = (
                    f"{interaction.user.mention} rolled and **won** — the miss "
                    f"never happened. Points refunded and the count is back to "
                    f"**{self.restore_count:,}**. Next number: "
                    f"**{self.restore_count + 1:,}**"
                )
            else:
                text = (
                    f"{interaction.user.mention} rolled and **won** — penalty "
                    "refunded. (The count already moved on, so the number stands.)"
                )
        else:
            # Lose: the penalty doubles (the second half, on top of the first).
            await self.cog.record_stat(self.guild_id, self.misser_id, points=-penalty)
            text = (
                f"{interaction.user.mention} rolled and **lost** — the penalty "
                f"doubles to **{penalty * 2}** points. Ouch."
            )

        await interaction.response.edit_message(content=text, view=self)
        self.stop()

    async def on_timeout(self) -> None:
        # Left it on the table: disable the button so it can't be pressed late.
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Counting(commands.Cog):
    """Channel counting game with hard/easy modes and milestones."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Cache of {guild_id: counting_channel_id} so the message listener
        # can skip non-counting channels without a database query.
        self._counting_channels: dict[int, int] = {}
        # Cache of {guild_id: {numbers with a custom image}} so a normal count
        # never hits the database — a number fires a celebration if it's a
        # built-in milestone OR someone set a custom image ("prize") for it.
        self._milestone_numbers: dict[int, set[int]] = {}

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
                high_score INT NOT NULL DEFAULT 0,
                last_user_id BIGINT NULL,
                active BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        # Multiple images per milestone are allowed (they rotate at random), so
        # this table has no unique constraint on (guild_id, number).
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_counting_milestones (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                number INT NOT NULL,
                media_url VARCHAR(500) NOT NULL,
                INDEX idx_guild_number (guild_id, number)
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

        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_counting_stats (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                points INT NOT NULL DEFAULT 0,
                correct INT NOT NULL DEFAULT 0,
                misses INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )

        # -- Migrations for installs created before phase 2 --------------------
        # Plain ALTERs in try/except so they work on MariaDB and MySQL 8 alike
        # (neither is reliable with IF NOT EXISTS on columns/indexes here).
        try:
            await self.db.execute(
                "ALTER TABLE vibe_counting ADD COLUMN high_score INT NOT NULL DEFAULT 0"
            )
        except Exception:
            pass  # column already exists
        try:
            # Old milestones table capped each number at one image; drop that
            # limit so multiple can rotate.
            await self.db.execute(
                "ALTER TABLE vibe_counting_milestones DROP INDEX unique_milestone"
            )
        except Exception:
            pass  # already dropped, or never had it

        rows = await self.db.fetchall("SELECT guild_id, channel_id FROM vibe_counting")
        self._counting_channels = {
            row["guild_id"]: row["channel_id"] for row in rows
        }

        # Warm the milestone-number cache from whatever images already exist.
        self._milestone_numbers = {}
        milestone_rows = await self.db.fetchall(
            "SELECT DISTINCT guild_id, number FROM vibe_counting_milestones"
        )
        for row in milestone_rows:
            self._milestone_numbers.setdefault(row["guild_id"], set()).add(
                row["number"]
            )

    async def record_stat(
        self, guild_id: int, user_id: int, *, points: int, correct: int = 0, miss: int = 0
    ) -> None:
        """Apply one scoring event to a member's leaderboard row.

        Deltas can be negative — a won gamble passes ``miss=-1`` to strike the
        miss from the member's record, so it reads as though it never happened.
        """
        await self.db.execute(
            "INSERT INTO vibe_counting_stats (guild_id, user_id, points, correct, misses) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE points = points + VALUES(points), "
            "correct = correct + VALUES(correct), misses = misses + VALUES(misses)",
            (guild_id, user_id, points, correct, miss),
        )

    async def restore_after_gamble(
        self, guild_id: int, restore_count: int | None, restore_last_user: int | None
    ) -> bool:
        """Undo a hard-mode reset when the gambler wins.

        Only restores when the count is still zero — if the community already
        started counting again during the gamble window, the fresh count wins
        and this leaves it alone (the win still refunds points either way).
        Returns True only when the count was actually rolled back.
        """
        if restore_count is None:
            return False  # easy mode: nothing was reset to begin with
        game = await self._get_game(guild_id)
        if game is None or game["current_count"] != 0:
            return False  # count already moved on since the reset
        await self.db.execute(
            "UPDATE vibe_counting SET current_count = %s, last_user_id = %s "
            "WHERE guild_id = %s",
            (restore_count, restore_last_user, guild_id),
        )
        return True

    async def _flavor_line(self, guild: discord.Guild) -> str:
        """A random real stat or trivia fact to soften a reset.

        Stat lines use display names rather than mentions so a reset never
        pings the person with the most misses — the call-out is in good fun,
        not a notification.
        """
        choices = list(TRIVIA_FACTS)

        game = await self._get_game(guild.id)
        if game and game.get("high_score"):
            choices.append(f"Best run so far: {game['high_score']:,}.")

        top = await self.db.fetchone(
            "SELECT user_id, correct FROM vibe_counting_stats "
            "WHERE guild_id = %s AND correct > 0 ORDER BY correct DESC LIMIT 1",
            (guild.id,),
        )
        if top:
            member = guild.get_member(top["user_id"])
            name = member.display_name if member else "someone"
            choices.append(f"Most correct counts: {name} ({top['correct']:,}).")

        flop = await self.db.fetchone(
            "SELECT user_id, misses FROM vibe_counting_stats "
            "WHERE guild_id = %s AND misses > 0 ORDER BY misses DESC LIMIT 1",
            (guild.id,),
        )
        if flop:
            member = guild.get_member(flop["user_id"])
            name = member.display_name if member else "someone"
            choices.append(f"Most misses: {name} ({flop['misses']:,}). We see you.")

        return random.choice(choices)

    async def _offer_gamble(
        self,
        message: discord.Message,
        restore_count: int | None,
        restore_last_user: int | None,
    ) -> None:
        """Post the Double-or-Nothing button for the member who just missed."""
        view = GambleView(
            self,
            misser_id=message.author.id,
            guild_id=message.guild.id,
            restore_count=restore_count,
            restore_last_user=restore_last_user,
        )
        try:
            view.message = await message.channel.send(
                f"{message.author.mention} — feeling lucky? **Double or Nothing** "
                "on that miss. Win and it never happened; lose and the penalty "
                f"doubles. ({GAMBLE_WINDOW_SECONDS}s)",
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    users=[discord.Object(id=message.author.id)]
                ),
            )
        except discord.HTTPException:
            pass

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
    async def start_game(self, ctx: commands.Context, start_at: int = 1) -> None:
        """Start the counting game, optionally from a given number.

        `$startgame` begins at 1. `$startgame 4127` sets the count so the next
        number posted is 4127 — useful when carrying a count over from another
        bot instead of starting the community over at zero.
        """
        game = await self._get_game(ctx.guild.id)
        if game is None:
            await ctx.send(
                embed=embeds.error(
                    "No counting channel is set. Run `$countingeasy` or "
                    "`$countinghard` in the channel you want first."
                )
            )
            return

        if start_at < 1:
            await ctx.send(embed=embeds.error("The starting number must be at least 1."))
            return

        # Store the number BEFORE the next expected one, since the game adds
        # one to find what it's waiting for.
        await self._reset_count(ctx.guild.id)
        await self.db.execute(
            "UPDATE vibe_counting SET active = TRUE, current_count = %s "
            "WHERE guild_id = %s",
            (start_at - 1, ctx.guild.id),
        )
        channel = ctx.guild.get_channel(game["channel_id"])
        target = channel.mention if channel else "the counting channel"
        await ctx.send(
            embed=embeds.success(
                f"Counting game started in {target}. Next number: **{start_at:,}**"
            )
        )

    @commands.command(name="milestone")
    @admin_only()
    async def set_milestone(self, ctx: commands.Context, number: int, url: str) -> None:
        """Add an image/gif to a milestone's rotation.

        Multiple images can share the same number — the bot picks one at random
        each time that milestone is hit, so a repeat never looks the same twice.
        """
        if number < 1:
            await ctx.send(embed=embeds.error("Milestone number must be positive."))
            return
        await self.db.execute(
            "INSERT INTO vibe_counting_milestones (guild_id, number, media_url) "
            "VALUES (%s, %s, %s)",
            (ctx.guild.id, number, url),
        )
        self._milestone_numbers.setdefault(ctx.guild.id, set()).add(number)
        count = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM vibe_counting_milestones "
            "WHERE guild_id = %s AND number = %s",
            (ctx.guild.id, number),
        )
        total = count["c"] if count else 1
        if total > 1:
            extra = f" It now rotates between **{total}** images."
        elif is_milestone(number):
            extra = ""
        else:
            extra = " This number will now fire a celebration when it's reached."
        await ctx.send(
            embed=embeds.success(f"Added a celebration image for **{number}**.{extra}")
        )

    @commands.command(name="milestones")
    @admin_only()
    async def list_milestones(self, ctx: commands.Context) -> None:
        """List every milestone image with its ID number (for removal)."""
        rows = await self.db.fetchall(
            "SELECT id, number, media_url FROM vibe_counting_milestones "
            "WHERE guild_id = %s ORDER BY number, id",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(
                embed=embeds.info(
                    "Milestone images",
                    "None set yet. Add one with `$milestone <number> <image url>`.",
                )
            )
            return
        lines = [
            f"**{row['number']:,}** — ID `{row['id']}` — {row['media_url']}"
            for row in rows
        ]
        await ctx.send(embed=embeds.info("Milestone images", "\n".join(lines)))

    @commands.command(name="removemilestone")
    @admin_only()
    async def remove_milestone(self, ctx: commands.Context, milestone_id: int) -> None:
        """Remove one milestone image by its ID (see $milestones)."""
        # Grab the number first so the cache can be pruned if it was the last
        # image for that number.
        target = await self.db.fetchone(
            "SELECT number FROM vibe_counting_milestones WHERE guild_id = %s AND id = %s",
            (ctx.guild.id, milestone_id),
        )
        removed = await self.db.execute(
            "DELETE FROM vibe_counting_milestones WHERE guild_id = %s AND id = %s",
            (ctx.guild.id, milestone_id),
        )
        if not removed:
            await ctx.send(embed=embeds.error(f"No milestone image with ID `{milestone_id}`."))
            return

        number = target["number"]
        still_there = await self.db.fetchone(
            "SELECT 1 FROM vibe_counting_milestones WHERE guild_id = %s AND number = %s",
            (ctx.guild.id, number),
        )
        if still_there is None:
            self._milestone_numbers.get(ctx.guild.id, set()).discard(number)
        await ctx.send(embed=embeds.success(f"Removed milestone image `{milestone_id}`."))

    # -- Leaderboard -------------------------------------------------------------

    @app_commands.command(
        name="countboard", description="Counting game leaderboard — who's carrying and who's crashing"
    )
    async def countboard(self, interaction: discord.Interaction) -> None:
        """Top counters by points, plus where the requester ranks."""
        rows = await self.db.fetchall(
            "SELECT user_id, points, correct, misses FROM vibe_counting_stats "
            "WHERE guild_id = %s ORDER BY points DESC, correct DESC "
            f"LIMIT {LEADERBOARD_SIZE}",
            (interaction.guild_id,),
        )
        if not rows:
            await interaction.response.send_message(
                "Nobody is on the board yet — go count something!"
            )
            return

        lines = [
            f"**{position}.** <@{row['user_id']}> — **{row['points']:,}** pts "
            f"({row['correct']:,} \N{HIGH VOLTAGE SIGN} / {row['misses']:,} \N{CROSS MARK})"
            for position, row in enumerate(rows, start=1)
        ]

        # The requester's own standing, even when they're outside the top 10.
        me = await self.db.fetchone(
            "SELECT points, correct, misses FROM vibe_counting_stats "
            "WHERE guild_id = %s AND user_id = %s",
            (interaction.guild_id, interaction.user.id),
        )
        footer = "Correct +1 · milestone +5 bonus · miss -10"
        if me is not None:
            above = await self.db.fetchone(
                "SELECT COUNT(*) AS c FROM vibe_counting_stats "
                "WHERE guild_id = %s AND (points > %s "
                "OR (points = %s AND correct > %s))",
                (interaction.guild_id, me["points"], me["points"], me["correct"]),
            )
            rank = above["c"] + 1
            footer = (
                f"Your rank: #{rank} · {me['points']:,} pts "
                f"({me['correct']:,} correct / {me['misses']:,} misses) · {footer}"
            )

        embed = embeds.info("Counting Leaderboard", "\n".join(lines))
        embed.set_footer(text=footer)
        await interaction.response.send_message(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
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
            # GREATEST keeps a running high-water mark for the "best run" stat.
            await self.db.execute(
                "UPDATE vibe_counting SET current_count = %s, last_user_id = %s, "
                "high_score = GREATEST(high_score, %s) WHERE guild_id = %s",
                (number, message.author.id, number, message.guild.id),
            )
            # A number celebrates if it's a built-in milestone OR an admin set
            # a custom "prize" image for it. Either way it pays the bonus.
            has_prize = number in self._milestone_numbers.get(message.guild.id, set())
            celebrate = is_milestone(number) or has_prize
            points = POINTS_CORRECT + (POINTS_MILESTONE_BONUS if celebrate else 0)
            await self.record_stat(
                message.guild.id, message.author.id, points=points, correct=1
            )
            try:
                await message.add_reaction(VERIFY_EMOJI)
            except discord.HTTPException:
                pass
            if celebrate:
                await self._celebrate(message, number)
            return

        # Miscount. Mark it, score it, then decide the consequence.
        try:
            await message.add_reaction(MISS_EMOJI)
        except discord.HTTPException:
            pass
        await self.record_stat(
            message.guild.id, message.author.id, points=POINTS_MISS, miss=1
        )

        # Double-counting has its own warning system in both modes: first
        # offence is a warning, a second offence resets the count to zero.
        # This is a rule-break rather than an honest miss, so no gamble.
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
                flavor = await self._flavor_line(message.guild)
                await message.channel.send(
                    f"{message.author.mention} counted twice in a row again "
                    "after a warning — the count resets to zero. "
                    f"Start again at **1**!\n\n{flavor}"
                )
            return

        # Honest wrong number from a different member. Hard mode resets (and we
        # capture the pre-miss state so a won gamble can put it back); easy mode
        # leaves the count standing. Either way the misser gets one shot at
        # Double or Nothing.
        restore_count: int | None = None
        restore_last_user: int | None = None
        if game["mode"] == "hard":
            restore_count = game["current_count"]
            restore_last_user = game["last_user_id"]
            await self._reset_count(message.guild.id)
            flavor = await self._flavor_line(message.guild)
            await message.channel.send(
                f"{message.author.mention} posted **{number}** but the next "
                f"number was **{expected}** — the count resets to zero. "
                f"Start again at **1**!\n\n{flavor}"
            )

        await self._offer_gamble(message, restore_count, restore_last_user)

    async def _celebrate(self, message: discord.Message, number: int) -> None:
        """Post a celebration for a milestone, rotating through any custom media."""
        rows = await self.db.fetchall(
            "SELECT media_url FROM vibe_counting_milestones "
            "WHERE guild_id = %s AND number = %s",
            (message.guild.id, number),
        )
        embed = embeds.info(
            f"{number:,}!",
            f"The count just hit **{number:,}** — nice work, everyone. "
            f"Next up: **{number + 1}**",
        )
        if rows:
            embed.set_image(url=random.choice(rows)["media_url"])
        try:
            await message.channel.send(embed=embed)
        except discord.HTTPException as exc:
            await self.bot.report_error(
                f"Failed to send a counting milestone in #{message.channel}",
                str(exc),
                guild=message.guild,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counting(bot))
