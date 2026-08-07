"""Tests for the staff help menu paging.

The menu is sent as code blocks, and Discord rejects any message over 2000
characters. As commands were added the menu passed that limit, the send failed,
and $viibr did nothing at all — with no error anyone would notice. These tests
make growing the menu past the limit a build failure instead.
"""

from __future__ import annotations

import pytest

from cogs import help as help_cog


def sent_length(page: str) -> int:
    """What the page actually costs once wrapped in a code fence."""
    return len(f"```\n{page}\n```")


# --------------------------------------------------------------------------- #
# The guarantee that matters
# --------------------------------------------------------------------------- #
def test_every_page_fits_in_a_discord_message():
    for index, page in enumerate(help_cog.build_menu_pages(), start=1):
        assert sent_length(page) <= help_cog.MENU_CHARACTER_LIMIT, (
            f"page {index} is {sent_length(page)} characters, over Discord's "
            f"{help_cog.MENU_CHARACTER_LIMIT} limit"
        )


def test_no_single_section_is_too_big_to_send():
    """A section is never split, so one oversized section would be unsendable."""
    budget = help_cog.MENU_CHARACTER_LIMIT - help_cog.CODE_FENCE_OVERHEAD
    for section in help_cog.MENU_SECTIONS:
        assert len(section) <= budget


# --------------------------------------------------------------------------- #
# Nothing is lost or duplicated by the paging
# --------------------------------------------------------------------------- #
def test_every_section_appears_exactly_once():
    combined = "\n".join(help_cog.build_menu_pages())
    for section in help_cog.MENU_SECTIONS:
        for line in section.strip().splitlines():
            assert combined.count(line) == 1, f"line missing or duplicated: {line}"


def test_the_header_leads_the_first_page():
    first = help_cog.build_menu_pages()[0]
    assert first.startswith("Viibr Bot Staff Commands")


@pytest.mark.parametrize(
    "command",
    [
        "$delete", "$setlog", "$errorchannel", "$sync",
        "$ticketstaff", "$ticketpanel", "$close",
        "$countinghard", "$startgame", "$milestone", "$milestonechance",
        "$removemilestone", "$roast", "$roasts", "$removeroast", "$roastchance",
        "$countblock", "$countblocks", "$countunblock",
        "$bdaychannel", "$wishchannel", "$bdaycard", "$removebdcard",
        "$addresource", "$deleteresource", "$replaceresource",
    ],
)
def test_command_still_documented(command):
    """Paging must not quietly drop a command from the menu."""
    assert command in "\n".join(help_cog.build_menu_pages())


# --------------------------------------------------------------------------- #
# The packer itself
# --------------------------------------------------------------------------- #
def test_sections_are_packed_rather_than_one_per_message():
    """Otherwise staff get a wall of tiny messages."""
    pages = help_cog.build_menu_pages()
    assert len(pages) < len(help_cog.MENU_SECTIONS)


def test_a_section_is_never_split_across_pages():
    pages = help_cog.build_menu_pages(
        header="", sections=["A" * 900, "B" * 900, "C" * 900], limit=2000
    )
    assert pages == ["A" * 900 + "\n" + "B" * 900, "C" * 900]


def test_an_unsendable_section_raises_instead_of_failing_silently():
    with pytest.raises(ValueError, match="over the"):
        help_cog.build_menu_pages(header="", sections=["X" * 5000], limit=2000)


def test_a_tiny_limit_still_produces_one_page_per_section():
    pages = help_cog.build_menu_pages(header="", sections=["A", "B", "C"], limit=10)
    assert pages == ["A", "B", "C"]


def test_the_joining_newline_is_counted_against_the_budget():
    """Two 2-character sections plus the newline between them is 5, so with a
    budget of 4 they cannot share a page."""
    pages = help_cog.build_menu_pages(header="", sections=["AA", "BB"], limit=12)
    assert pages == ["AA", "BB"]
