"""Tests for mention safety.

Member-supplied text reaches public messages in several features (birthday
song titles, birthday wishes). Discord parses @everyone and role mentions out
of message content unless allowed_mentions forbids it, so a member could make
the bot ping the whole server.

This has now been fixed twice, both times only in the file someone happened to
be looking at. These tests cover the rule itself rather than one call site.
"""

from __future__ import annotations

import ast
import pathlib

import discord
import pytest

from utils.mentions import only_ping, only_ping_role


REPO = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# The helpers
# --------------------------------------------------------------------------- #
def test_only_ping_forbids_everyone_and_roles():
    allowed = only_ping(123)
    assert allowed.everyone is False
    assert allowed.roles is False
    assert [o.id for o in allowed.users] == [123]


def test_only_ping_accepts_several_members():
    allowed = only_ping(1, 2, 3)
    assert [o.id for o in allowed.users] == [1, 2, 3]


def test_only_ping_with_no_arguments_pings_nobody():
    allowed = only_ping()
    assert allowed.everyone is False
    assert allowed.roles is False
    assert allowed.users == []


def test_only_ping_role_allows_just_that_role():
    role = discord.Object(id=999)
    allowed = only_ping_role(role)
    assert allowed.everyone is False
    assert allowed.users is False
    assert allowed.roles == [role]


# --------------------------------------------------------------------------- #
# The bot-wide default
# --------------------------------------------------------------------------- #
def test_bot_disables_everyone_and_role_pings_by_default():
    """app.py must pass allowed_mentions to the Bot constructor. Without it a
    send that forgets the keyword lets Discord parse @everyone out of whatever
    text it contains."""
    source = (REPO / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        if "allowed_mentions" not in kwargs or "command_prefix" not in kwargs:
            continue
        call = next(k.value for k in node.keywords if k.arg == "allowed_mentions")
        rendered = ast.unparse(call)
        assert "everyone=False" in rendered, rendered
        assert "roles=False" in rendered, rendered
        return

    pytest.fail("app.py does not pass allowed_mentions when constructing the bot")


# --------------------------------------------------------------------------- #
# The call sites that carry member-typed text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "needle",
    [
        # /birthdaysong announcement — the title is whatever the member typed
        "text, allowed_mentions=only_ping(interaction.user.id)",
        # the daily announcement replays that stored title
        "allowed_mentions=only_ping(*pingable)",
    ],
)
def test_birthday_sends_restrict_mentions(needle):
    source = (REPO / "cogs" / "birthdays.py").read_text(encoding="utf-8")
    assert needle in source, f"missing mention guard: {needle}"


def test_ticket_staff_ping_opts_back_in():
    """The bot-wide default blocks role pings, so the staff notification has to
    allow its own role explicitly or it silently stops notifying anyone."""
    source = (REPO / "cogs" / "tickets.py").read_text(encoding="utf-8")
    assert "allowed_mentions=only_ping_role(staff_role)" in source


def test_no_cog_builds_allowed_mentions_the_unsafe_way():
    """discord.AllowedMentions(users=[...]) leaves everyone and roles enabled.
    Every cog should go through utils.mentions instead."""
    offenders = []
    for path in sorted((REPO / "cogs").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func).endswith("AllowedMentions"):
                kwargs = {k.arg for k in node.keywords if k.arg}
                # .none() and .all() are attribute calls, not this constructor.
                if "users" in kwargs and not {"everyone", "roles"} <= kwargs:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "AllowedMentions built without everyone/roles disabled at: "
        + ", ".join(offenders)
    )
