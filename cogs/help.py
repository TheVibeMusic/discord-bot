"""Help commands.

`$help` / `$viibr` sends the staff command menu (a code block) to admins/mods;
members who run it are pointed to the `/help` slash command. `/help` is the
member-facing overview and never lists admin commands.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils import embeds
from utils.permissions import is_mod

# Bot developer, tagged in the member help menu for problem reports.
DEV_ID = 966507927756234823

# Discord rejects any message over 2000 characters, and the staff menu passed
# that as commands were added — the send failed and $viibr did nothing at all.
# The menu is now stored as sections and split across as many code blocks as it
# takes, so growing it can never silently break the command again.
MENU_CHARACTER_LIMIT = 2000
CODE_FENCE_OVERHEAD = 8  # ```\n ... \n```

MENU_HEADER = """\
Viibr Bot Staff Commands - Customized Utility Bot, Responds only to the listed commands.
Prefix for staff is the dollar sign ▪ $
Members help menu accessed with the command /help
"""

MENU_SECTIONS = [
    """\
  General
$help/$viibr (admin/mod) - this menu
$delete <#> - (admin) bulk delete # amount of messages up to 100
$setlog <channel ID> (admin) - sets deleted msgs accountability channel
$errorchannel <channel ID> (admin) - sets where bot error reports post
$sync – sync slash commands
""",
    """\
 Tickets Setup (admin)
$ticketstaff @role - set role to be pinged
$ticketcategory - set tickets category
$ticketlog <channel ID> - logging channel for closed tickets
$ticketpanel - post tickets button in this channel
$ticketconfig - the current ticket setup
$close (admin/mods) - close the ticket in the current channel
""",
    """\
  Counting (admin)
$countinghard/$countingeasy - set counting channel with difficulty level (hard mode = wrong count resets to 0)
$startgame - starts/restarts counting (or $startgame <number> to pick up an existing count, e.g. $startgame 4127)
$milestone <number> <url> - add a pic/prize to any number (add several to one number = random pick each time)
   ・ 100/200/500 and every 1000 are permanent - they always fire
   ・ any other number is a prize - it only fires part of the time, so it stays a surprise
$milestonechance <percent> - how often prize numbers fire (default 10%)
$milestones - list milestone pics with their ID numbers
$removemilestone <ID> - remove a milestone pic from the rotation
$roast <image url> - add a roast pic, posted sometimes when someone miscounts (images only)
$roasts - list roast pics with their ID numbers
$removeroast <ID> - remove a roast pic
$roastchance <percent> - how often a miscount gets roasted (default 25%, 0 turns it off)
$shame <image url> - add a pic posted when someone deletes their count (images only)
$shames - list shaming pics with their ID numbers
$removeshame <ID> - remove a shaming pic
$countblocks - who has strikes or an active block
$countunblock @member - lift a block and clear their strikes
   ・ deleting your own number = shaming pic + blocked from counting
   ・ 1st time 2 hours, 2nd time a day, 3rd time permanent
""",
    """\
  Birthdays (admin)
$bdaychannel <channelID> - set the channel for birthday announcements members can set their own birthdays in this channel as well with a slash command /addmybd
$wishchannel <channelID> - set the channel where the send-wishes button posts (wishes themselves go to the announcement channel)
$bdsongs – list community-submitted birthday songs with their ID numbers
$removebdsong <ID> – remove a submitted song that breaks the rules
$bdaycard <image url> – add a greeting card to the rotation (Imgur direct link, ends in .png/.jpg/.gif)
$bdaycards – list the cards in the rotation with their ID numbers
$removebdcard <ID> – remove a card from the rotation
""",
    """\
  Resources (admin)
$addresource [Name](url) - add link to the /resources list
$deleteresource [Name] - remove this resource
$replaceresource [Name](new url) - update a resource link
""",
]


def build_menu_pages(
    header: str = MENU_HEADER,
    sections: list[str] = MENU_SECTIONS,
    limit: int = MENU_CHARACTER_LIMIT,
) -> list[str]:
    """Pack the menu sections into as few code blocks as Discord allows.

    A section is never split across two messages, so each block reads as a
    complete group of commands. Raises if a single section cannot fit on its
    own, which is a authoring error rather than something to hide at runtime.
    """
    budget = limit - CODE_FENCE_OVERHEAD
    pages: list[str] = []
    current = header

    for section in sections:
        if len(section) > budget:
            raise ValueError(
                f"A single help section is {len(section)} characters, over the "
                f"{budget} available inside one Discord message. Split it."
            )
        # The blank line joining two sections costs a character too; leaving it
        # out of the sum lets a page overflow by one per section.
        addition = len(section) + (1 if current else 0)
        if len(current) + addition > budget:
            pages.append(current.rstrip("\n"))
            current = section
        else:
            current += "\n" + section if current else section

    if current.strip():
        pages.append(current.rstrip("\n"))
    return pages

MEMBER_ABOUT = (
    "Customized Utility bot for this server. Members can use these slash commands:"
)

MEMBER_TICKET = (
    "**Head over to the tickets channel and hit the Open A Ticket button**, "
    "then look for the private channel that pops up in the tickets category "
    "with your username as the channel name. You can send your message to the "
    "staff there and someone will be there to help you as soon as possible."
)

MEMBER_COMMANDS = (
    "`/resources` — server links: website, Twitch, Reddit, YouTube\n"
    "`/addmybd` — add your birthday to the server calendar\n"
    "`/removemybd` — take your birthday off the calendar\n"
    "`/calendar` — see upcoming birthdays\n"
    "`/birthdaysong` — submit a birthday song you made to the rotation\n"
    "`/countboard` — counting game leaderboard and your rank"
)

MEMBER_COUNTING = (
    "Join the counting game in the counting channel — post the next number, "
    "one per person. \N{HIGH VOLTAGE SIGN} means you got it, and big numbers "
    "get a celebration. Miss a number and you can hit **Double or Nothing** "
    "for a chance to undo it — or double the damage. No counting twice in a "
    "row — you get one warning, then the count resets to zero. And no chatting "
    "in there: messages without the number get removed. And don't delete your "
    "own number — it leaves everyone guessing what comes next, so it gets you "
    "blocked from counting: two hours the first time, a day the second, and "
    "permanently after that."
)

MEMBER_REPORT = (
    "**Notice any glitches or the commands not working for the bot?** "
    f"Tag <@{DEV_ID}> in the channel where your command failed with a note "
    "about what happened. Thank you!"
)

# Invisible field header (zero-width space) so the report note reads as its
# own paragraph without a visible title.
BLANK_FIELD_NAME = "​"


class Help(commands.Cog):
    """Help menus for staff and members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help", aliases=["viibr"])
    async def help_command(self, ctx: commands.Context) -> None:
        """Send the staff menu. Members are pointed to /help.

        Sent as several code blocks: the full menu is past Discord's 2000
        character limit for one message.
        """
        if not (isinstance(ctx.author, discord.Member) and is_mod(ctx.author)):
            await ctx.send("That menu is for staff. Use `/help` to see your commands.")
            return

        for page in build_menu_pages():
            await ctx.send(f"```\n{page}\n```")

    @app_commands.command(
        name="help", description="What Viibr can do and how to use it"
    )
    async def slash_help(self, interaction: discord.Interaction) -> None:
        """Member-facing overview of the bot. Never lists admin commands."""
        embed = embeds.info("Viibr Bot Slash Commands", MEMBER_ABOUT)
        embed.add_field(name="/Ticket", value=MEMBER_TICKET, inline=False)
        embed.add_field(name="Slash commands", value=MEMBER_COMMANDS, inline=False)
        embed.add_field(name="Counting", value=MEMBER_COUNTING, inline=False)
        embed.add_field(name=BLANK_FIELD_NAME, value=MEMBER_REPORT, inline=False)
        embed.set_footer(text="More features are on the way!")
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
