from __future__ import annotations

from .base import Provider

NAMES = ("spotify", "ytmusic")


def get_provider(name: str) -> Provider:
    if name == "spotify":
        from .spotify import SpotifyProvider

        return SpotifyProvider()
    if name == "ytmusic":
        from .ytmusic import YTMusicProvider

        return YTMusicProvider()
    raise ValueError(f"unknown service {name!r} (choose from {', '.join(NAMES)})")
