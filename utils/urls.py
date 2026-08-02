"""Shared link validation for admin commands that accept image URLs.

Several commands ($bdaycard, $milestone, $roast) take a URL that Discord has
to render as an image. They all need the same two checks, so they live here
rather than being repeated per cog.
"""

from __future__ import annotations

IMAGE_URL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def is_http_url(url: str) -> bool:
    """True when the string is an http(s) link rather than arbitrary text."""
    return url.startswith(("http://", "https://"))


def is_direct_image_url(url: str) -> bool:
    """True when the link points straight at an image file.

    Query strings are ignored, so a CDN link such as
    ``https://i.imgur.com/a.png?width=600`` still counts. A gallery or page
    link like ``https://imgur.com/gallery/abc`` does not, because Discord
    cannot embed it.
    """
    return is_http_url(url) and url.lower().split("?")[0].endswith(IMAGE_URL_SUFFIXES)
