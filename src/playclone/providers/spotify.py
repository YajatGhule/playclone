from __future__ import annotations

import os

import spotipy
from spotipy.cache_handler import CacheFileHandler

from ..config import config_dir, load_settings
from ..models import LIKED, Playlist, Track

SCOPES = "playlist-read-private playlist-read-collaborative playlist-modify-private user-library-read"
DEFAULT_REDIRECT = "http://127.0.0.1:8888/callback"  # Spotify no longer accepts "localhost"


def _client_id() -> str:
    cid = os.environ.get("SPOTIFY_CLIENT_ID") or load_settings().get("spotify_client_id")
    if not cid:
        raise SystemExit("No Spotify client id. Run: playclone auth spotify --client-id <ID>")
    return cid


def make_auth(open_browser: bool = True, redirect_uri: str | None = None) -> spotipy.SpotifyPKCE:
    return spotipy.SpotifyPKCE(
        client_id=_client_id(),
        redirect_uri=redirect_uri or os.environ.get("SPOTIFY_REDIRECT_URI")
        or load_settings().get("spotify_redirect_uri") or DEFAULT_REDIRECT,
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(config_dir() / "spotify-token.json")),
        open_browser=open_browser,
    )


def _to_track(t: dict) -> Track | None:
    if not t or t.get("type", "track") != "track" or t.get("is_local") or not t.get("id"):
        return None
    return Track(
        id=t["id"],
        title=t["name"],
        artists=tuple(a["name"] for a in t.get("artists", [])),
        album=(t.get("album") or {}).get("name"),
        duration_s=round(t["duration_ms"] / 1000) if t.get("duration_ms") else None,
        isrc=(t.get("external_ids") or {}).get("isrc"),
    )


def _clean_query(s: str) -> str:
    return s.replace('"', " ").replace(":", " ")


class SpotifyProvider:
    name = "spotify"

    def __init__(self, client: spotipy.Spotify | None = None):
        self.sp = client or spotipy.Spotify(auth_manager=make_auth(), retries=5, status_retries=5)

    def _pages(self, page: dict):
        while page:
            yield from page.get("items", [])
            page = self.sp.next(page) if page.get("next") else None

    def list_playlists(self) -> list[Playlist]:
        out = []
        for p in self._pages(self.sp.current_user_playlists(limit=50)):
            if not p:
                continue
            count = (p.get("items") or p.get("tracks") or {}).get("total")
            out.append(Playlist(p["id"], p["name"], p.get("description") or "", count))
        return out

    def get_tracks(self, playlist_id: str) -> list[Track]:
        if playlist_id == LIKED:
            page = self.sp.current_user_saved_tracks(limit=50)
        else:
            page = self.sp.playlist_items(playlist_id, limit=50, additional_types=("track",))
        tracks = []
        for item in self._pages(page):
            t = _to_track(item.get("track") or item.get("item"))
            if t:
                tracks.append(t)
        return tracks

    def search(self, track: Track) -> list[Track]:
        title = _clean_query(track.title)
        artist = _clean_query(track.artists[0]) if track.artists else ""
        queries = []
        if track.isrc:
            queries.append(f"isrc:{track.isrc}")
        queries.append(f"track:{title} artist:{artist}" if artist else f"track:{title}")
        queries.append(f"{title} {artist}".strip())

        seen, out = set(), []
        for q in queries:
            items = self.sp.search(q, limit=10, type="track").get("tracks", {}).get("items", [])
            for item in items:
                t = _to_track(item)
                if t and t.id not in seen:
                    seen.add(t.id)
                    out.append(t)
            if out and q.startswith("isrc:"):
                break  # exact recording found, skip fuzzy queries
        return out

    def create_playlist(self, name: str, description: str, track_ids: list[str]) -> str:
        pl = self.sp.current_user_playlist_create(name, public=False, description=description[:300])
        self.add_tracks(pl["id"], track_ids)
        return pl["id"]

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        for i in range(0, len(track_ids), 100):
            self.sp.playlist_add_items(playlist_id, [f"spotify:track:{t}" for t in track_ids[i : i + 100]])
