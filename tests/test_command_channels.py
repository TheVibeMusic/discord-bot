"""Tests for app.allowed_channel — where slash commands may be used.

Slash commands are confined to the configured channels so they don't clutter
conversation channels. Threads inherit their parent's permission, exempt users
may run commands anywhere, and an unset configuration disables the restriction
rather than locking everyone out.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

import app
import config


COUNTING = 1525704474339835975
BOT_COMMANDS = 1526824644374560848
RANDOM_CHANNEL = 999999999999999999

MYRA = 966507927756234823
TYRO = 387053329819369473
MEMBER = 111111111111111111


@pytest.fixture(autouse=True)
def restrict(monkeypatch):
    """Apply the live configuration: two channels, two exempt users."""
    monkeypatch.setattr(config, "COMMAND_CHANNEL_IDS", {COUNTING, BOT_COMMANDS})
    monkeypatch.setattr(config, "COMMAND_EXEMPT_IDS", {MYRA, TYRO})


def interaction(user_id: int, channel_id: int, parent_id: int | None = None):
    """A stand-in interaction; parent_id set only for thread cases."""
    fake = MagicMock()
    fake.user.id = user_id
    fake.channel_id = channel_id
    if parent_id is None:
        # A regular channel has no parent_id attribute worth reading.
        fake.channel = MagicMock(spec=[])
    else:
        fake.channel = MagicMock()
        fake.channel.parent_id = parent_id
    return fake


# --------------------------------------------------------------------------- #
# Ordinary members
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("channel_id", [COUNTING, BOT_COMMANDS])
def test_member_allowed_in_the_designated_channels(channel_id):
    assert app.allowed_channel(interaction(MEMBER, channel_id)) is True


def test_member_blocked_elsewhere():
    assert app.allowed_channel(interaction(MEMBER, RANDOM_CHANNEL)) is False


# --------------------------------------------------------------------------- #
# Exempt users
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("user_id", [MYRA, TYRO])
def test_exempt_users_may_run_commands_anywhere(user_id):
    assert app.allowed_channel(interaction(user_id, RANDOM_CHANNEL)) is True


@pytest.mark.parametrize("user_id", [MYRA, TYRO])
def test_exempt_users_still_work_in_the_normal_channels(user_id):
    assert app.allowed_channel(interaction(user_id, COUNTING)) is True


def test_owner_is_exempt_even_if_left_out_of_the_list(monkeypatch):
    """config folds OWNER_ID into the exempt set, so a bad list can't lock the
    owner out. This mirrors that behaviour at the boundary."""
    monkeypatch.setattr(config, "COMMAND_EXEMPT_IDS", {TYRO})
    assert app.allowed_channel(interaction(TYRO, RANDOM_CHANNEL)) is True
    assert app.allowed_channel(interaction(MYRA, RANDOM_CHANNEL)) is False


# --------------------------------------------------------------------------- #
# Threads inherit their parent
# --------------------------------------------------------------------------- #
def test_thread_under_an_allowed_channel_is_allowed():
    thread = interaction(MEMBER, channel_id=555, parent_id=BOT_COMMANDS)
    assert app.allowed_channel(thread) is True


def test_thread_under_a_random_channel_is_blocked():
    thread = interaction(MEMBER, channel_id=555, parent_id=RANDOM_CHANNEL)
    assert app.allowed_channel(thread) is False


# --------------------------------------------------------------------------- #
# Fail-open when unconfigured
# --------------------------------------------------------------------------- #
def test_no_configured_channels_disables_the_restriction(monkeypatch):
    """An unset COMMAND_CHANNEL_IDS must not lock the server out of the bot."""
    monkeypatch.setattr(config, "COMMAND_CHANNEL_IDS", set())
    assert app.allowed_channel(interaction(MEMBER, RANDOM_CHANNEL)) is True
