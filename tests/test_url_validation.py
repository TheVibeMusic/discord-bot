"""Tests for utils.urls — the image-link check shared by admin commands.

$bdaycard, $milestone, and $roast all store a URL that Discord must render as
an image. Anything that isn't a direct image link produces an empty embed at
the worst possible moment, so the commands reject it up front.
"""

from __future__ import annotations

import pytest

from utils.urls import is_direct_image_url, is_http_url


# --------------------------------------------------------------------------- #
# Links that should be accepted
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "https://i.imgur.com/abc123.png",
        "https://i.imgur.com/abc123.jpg",
        "https://i.imgur.com/abc123.jpeg",
        "https://i.imgur.com/abc123.gif",
        "https://i.imgur.com/abc123.webp",
        "http://example.test/pic.png",
        "https://cdn.discordapp.com/attachments/1/2/image.png",
    ],
)
def test_direct_image_links_are_accepted(url):
    assert is_direct_image_url(url) is True


def test_query_string_is_ignored():
    """Discord CDN links carry size parameters after the extension."""
    url = "https://cdn.discordapp.com/attachments/1/2/a.png?ex=abc&width=600"
    assert is_direct_image_url(url) is True


def test_uppercase_extension_is_accepted():
    assert is_direct_image_url("https://example.test/PIC.PNG") is True


# --------------------------------------------------------------------------- #
# Garbage that should be rejected
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    [
        "banana",                                # plain text
        "",                                      # nothing at all
        "   ",                                   # whitespace
        "imgur.com/a.png",                       # no scheme
        "ftp://example.test/a.png",              # wrong scheme
        "javascript:alert(1)",                   # not a link at all
        "https://imgur.com/gallery/abc123",      # gallery page, not the image
        "https://example.test/",                 # no file
        "https://example.test/page.html",        # wrong file type
        "https://example.test/video.mp4",        # not an image
        "https://example.test/a.png.exe",        # suffix buried mid-name
    ],
)
def test_non_image_links_are_rejected(value):
    assert is_direct_image_url(value) is False


# --------------------------------------------------------------------------- #
# The scheme-only check used for the friendlier first error message
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.test/anything", True),
        ("http://example.test/anything", True),
        ("example.test/anything", False),
        ("banana", False),
        ("", False),
    ],
)
def test_is_http_url(url, expected):
    assert is_http_url(url) is expected


def test_a_page_link_passes_the_scheme_check_but_not_the_image_check():
    """This split is why there are two functions: the cog can tell the user
    'that isn't a link' separately from 'that isn't an image'."""
    url = "https://imgur.com/gallery/abc123"
    assert is_http_url(url) is True
    assert is_direct_image_url(url) is False
