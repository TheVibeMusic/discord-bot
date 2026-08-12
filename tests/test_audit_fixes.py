"""Tests for the fixes from the full audit.

Each of these was a failure that produced no error — the bot carried on
looking fine while doing the wrong thing. They are grouped here because they
share that shape rather than a module.
"""

from __future__ import annotations

import ast
import pathlib

import discord
import pytest
from unittest.mock import AsyncMock, MagicMock

from utils import embeds


REPO = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Listing commands must not outgrow the embed description limit
# --------------------------------------------------------------------------- #
def test_short_list_is_a_single_page():
    pages = embeds.paged("Title", [f"line {n}" for n in range(10)])
    assert len(pages) == 1
    assert pages[0].footer.text is None  # no page counter when there's one page


def test_a_long_list_splits_instead_of_overflowing():
    lines = [f"`#{n}` [card](https://i.imgur.com/abcdefgh.png) — added by <@{n}>"
             for n in range(300)]
    pages = embeds.paged("Birthday cards", lines)

    assert len(pages) > 1
    for page in pages:
        assert len(page.description) <= embeds.DESCRIPTION_LIMIT


def test_no_line_is_lost_when_paging():
    """These listings are the only place the removal ids appear, so a dropped
    line means an entry that can't be removed."""
    lines = [f"`#{n}` entry number {n}" for n in range(400)]
    pages = embeds.paged("Title", lines)

    rendered = "\n".join(p.description for p in pages)
    for line in lines:
        assert line in rendered


def test_pages_are_numbered_when_there_is_more_than_one():
    pages = embeds.paged("Title", ["x" * 500 for _ in range(30)])
    assert len(pages) > 1
    assert pages[0].footer.text == f"Page 1 of {len(pages)}"


def test_a_single_over_long_line_is_truncated_not_dropped():
    pages = embeds.paged("Title", ["y" * 9000])
    assert len(pages) == 1
    assert len(pages[0].description) <= embeds.DESCRIPTION_LIMIT
    assert pages[0].description.endswith("\N{HORIZONTAL ELLIPSIS}")


def test_empty_list_produces_no_pages():
    assert embeds.paged("Title", []) == []


@pytest.mark.parametrize(
    "source_file,marker",
    [
        ("cogs/counting.py", 'embeds.paged("Roast images"'),
        ("cogs/counting.py", 'embeds.paged("Shaming images"'),
        ("cogs/counting.py", 'embeds.paged("Milestone images"'),
        ("cogs/counting.py", 'embeds.paged("Counting blocks"'),
        ("cogs/birthdays.py", "embeds.paged(title, lines)"),
        ("cogs/birthdays.py", 'embeds.paged(f"Birthday cards'),
    ],
)
def test_growing_listings_go_through_the_pager(source_file, marker):
    source = (REPO / source_file).read_text(encoding="utf-8")
    assert marker in source, f"{source_file} no longer pages: {marker}"


# --------------------------------------------------------------------------- #
# Background tasks must not die silently
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("loop_name", ["announce_birthdays", "sweep_expired_pins"])
def test_every_background_loop_has_an_error_handler(loop_name):
    """A discord.ext task that raises stops permanently. Birthdays would just
    quietly never happen again."""
    source = (REPO / "cogs" / "birthdays.py").read_text(encoding="utf-8")
    assert f"@{loop_name}.error" in source, f"{loop_name} has no error handler"
    assert f"@{loop_name}.before_loop" in source, f"{loop_name} has no before_loop"


def test_the_error_handlers_restart_their_loop():
    source = (REPO / "cogs" / "birthdays.py").read_text(encoding="utf-8")
    assert "self.announce_birthdays.restart()" in source
    assert "self.sweep_expired_pins.restart()" in source


def test_one_bad_birthday_does_not_cancel_the_others():
    """The per-row send is wrapped, so a single failure can't abort the day."""
    source = (REPO / "cogs" / "birthdays.py").read_text(encoding="utf-8")
    start = source.index("async def announce_birthdays")
    body = source[start : source.index("async def _send_greeting")]
    assert "try:" in body and "_send_greeting" in body


# --------------------------------------------------------------------------- #
# Slash commands must report failures
# --------------------------------------------------------------------------- #
def test_slash_errors_reach_the_user_and_the_error_channel():
    source = (REPO / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_error"
    )
    body = ast.unparse(handler)

    assert "report_error" in body, "slash failures never reach the error channel"
    assert "is_done()" in body, "must choose followup vs response"
    assert "followup.send" in body and "response.send_message" in body


def test_slash_check_failures_stay_quiet():
    """interaction_check already replied; a second message would be noise."""
    source = (REPO / "app.py").read_text(encoding="utf-8")
    assert "if isinstance(error, app_commands.CheckFailure):\n            return" in source


# --------------------------------------------------------------------------- #
# A ticket channel must not be deleted when its transcript wasn't saved
# --------------------------------------------------------------------------- #
def test_unsaved_transcript_keeps_the_channel():
    source = (REPO / "cogs" / "tickets.py").read_text(encoding="utf-8")
    start = source.index("async def _finalize_close")
    body = source[start:]

    assert "transcript_filed" in body, "no record of whether the transcript saved"
    # The early return must come before the delete.
    guard = body.index("if not transcript_filed:")
    delete = body.index("await channel.delete(")
    assert guard < delete, "the channel is deleted before the guard runs"


def test_a_missing_log_channel_is_an_error_not_a_skip():
    """A stored id that no longer resolves used to skip the transcript
    silently, then delete the channel anyway."""
    source = (REPO / "cogs" / "tickets.py").read_text(encoding="utf-8")
    assert "raise RuntimeError(" in source
    assert "re-run $ticketlog" in source


# --------------------------------------------------------------------------- #
# An oversized song must not cancel the whole birthday
# --------------------------------------------------------------------------- #
def test_song_attachment_is_checked_against_the_guild_limit():
    source = (REPO / "cogs" / "birthdays.py").read_text(encoding="utf-8")
    assert "guild.filesize_limit" in source, "still assuming a hardcoded limit"
    assert "ATTACHMENT_HEADROOM_BYTES" in source


def test_an_oversized_song_falls_back_to_a_link():
    source = (REPO / "cogs" / "birthdays.py").read_text(encoding="utf-8")
    start = source.index("budget = channel.guild.filesize_limit")
    body = source[start : start + 700]
    # The else branch posts the url rather than skipping the greeting.
    assert "content += f\"{credit}\\n{pick['url']}\"" in body
