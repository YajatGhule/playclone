from __future__ import annotations

import re

from ytmusicapi import YTMusic

from ..config import config_dir
from ..models import LIKED, Playlist, Track

# Auto-generated library entries that aren't real playlists.
_SKIP_IDS = {"LM", "SE"}


def auth_file():
    return config_dir() / "ytmusic-browser.json"


def _to_track(t: dict) -> Track | None:
    if not t or not t.get("videoId") or t.get("isAvailable") is False:
        return None
    album = t.get("album")
    return Track(
        id=t["videoId"],
        title=t.get("title") or "",
        artists=tuple(a["name"] for a in (t.get("artists") or []) if a.get("name")),
        album=album.get("name") if isinstance(album, dict) else album,
        duration_s=t.get("duration_seconds"),
    )


class YTMusicProvider:
    name = "ytmusic"

    def __init__(self, client: YTMusic | None = None):
        if client is None:
            path = auth_file()
            if not path.exists():
                raise SystemExit("Not signed in to YouTube Music. Run: playclone auth ytmusic")
            client = YTMusic(str(path))
        self.yt = client

    def list_playlists(self) -> list[Playlist]:
        return [
            Playlist(p["playlistId"], p["title"], p.get("description") or "", _count(p.get("count")))
            for p in self.yt.get_library_playlists(limit=None)
            if p.get("playlistId") not in _SKIP_IDS
        ]

    def get_tracks(self, playlist_id: str) -> list[Track]:
        if playlist_id == LIKED:
            data = self.yt.get_liked_songs(limit=100_000)
        else:
            data = self.yt.get_playlist(playlist_id, limit=None)
        return [t for t in map(_to_track, data.get("tracks", [])) if t]

    def search(self, track: Track) -> list[Track]:
        query = f"{track.title} {track.artists[0] if track.artists else ''}".strip()
        out, seen = [], set()
        # Prefer official song uploads; fall back to music videos for tracks only on YouTube.
        for kind in ("songs", "videos"):
            for r in self.yt.search(query, filter=kind, limit=10):
                t = _to_track(r)
                if t and t.id not in seen:
                    seen.add(t.id)
                    out.append(t)
            if out:
                break
        return out

    def create_playlist(self, name: str, description: str, track_ids: list[str]) -> str:
        # YouTube rejects descriptions containing angle brackets.
        res = self.yt.create_playlist(name, re.sub(r"[<>]", "", description)[:5000], privacy_status="PRIVATE")
        if not isinstance(res, str):
            raise RuntimeError(f"YouTube Music refused to create the playlist: {res}")
        self.add_tracks(res, track_ids)
        return res

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        for i in range(0, len(track_ids), 100):
            self.yt.add_playlist_items(playlist_id, track_ids[i : i + 100], duplicates=True)


def _count(value) -> int | None:
    if isinstance(value, int):
        return value
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None
