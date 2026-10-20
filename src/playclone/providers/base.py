from __future__ import annotations

from typing import Protocol

from ..models import Playlist, Track


class Provider(Protocol):
    name: str

    def list_playlists(self) -> list[Playlist]:
        """The user's playlists. Liked songs are always available as id ``LIKED``."""
        ...

    def get_tracks(self, playlist_id: str) -> list[Track]: ...

    def search(self, track: Track) -> list[Track]:
        """Catalog candidates for ``track``, best first."""
        ...

    def create_playlist(self, name: str, description: str, track_ids: list[str]) -> str:
        """Create a private playlist containing ``track_ids`` and return its id."""
        ...

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Append ``track_ids`` to an existing playlist."""
        ...
