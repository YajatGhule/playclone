"""Adapters against fake API clients shaped like real spotipy / ytmusicapi responses."""

from playclone.models import LIKED, Track
from playclone.providers.spotify import SpotifyProvider
from playclone.providers.ytmusic import YTMusicProvider


def sp_track(id, name, artist, ms=200_000, isrc=None, **extra):
    return {"type": "track", "id": id, "name": name, "artists": [{"name": artist}],
            "album": {"name": "Alb"}, "duration_ms": ms, "external_ids": {"isrc": isrc} if isrc else {}, **extra}


class FakeSpotify:
    def __init__(self):
        self.added = []

    def current_user_playlists(self, limit):
        return {"items": [{"id": "p1", "name": "Gym", "description": "", "tracks": {"total": 3}}], "next": "page2"}

    def next(self, page):
        if page.get("next") == "page2":
            return {"items": [{"id": "p2", "name": "Chill", "description": "x", "items": {"total": 1}}], "next": None}
        return {"items": [{"track": sp_track("t3", "Third", "C")}], "next": None}

    def playlist_items(self, pid, limit, additional_types):
        return {"items": [
            {"track": sp_track("t1", "One", "A", isrc="ISRC1")},
            {"item": sp_track("t2", "Two", "B")},           # newer API key
            {"track": sp_track(None, "Local", "L", is_local=True)},
            {"track": None},
        ], "next": "more"}

    def current_user_saved_tracks(self, limit):
        return {"items": [{"track": sp_track("l1", "Liked", "A")}], "next": None}

    def search(self, q, limit, type):
        return {"tracks": {"items": [sp_track("found", "One", "A", isrc="ISRC1")] if q.startswith("isrc:") else []}}

    def current_user_playlist_create(self, name, public, description):
        assert public is False
        return {"id": "newpl"}

    def playlist_add_items(self, pid, uris):
        self.added.append(uris)


def test_spotify_adapter():
    fake = FakeSpotify()
    sp = SpotifyProvider(client=fake)
    assert [(p.id, p.count) for p in sp.list_playlists()] == [("p1", 3), ("p2", 1)]
    assert [t.id for t in sp.get_tracks("p1")] == ["t1", "t2", "t3"]
    assert sp.get_tracks("p1")[0].isrc == "ISRC1"
    assert sp.get_tracks(LIKED)[0].id == "l1"
    assert [t.id for t in sp.search(Track("t1", "One", ("A",), isrc="ISRC1"))] == ["found"]
    assert sp.create_playlist("N", "d", [f"id{i}" for i in range(150)]) == "newpl"
    assert [len(b) for b in fake.added] == [100, 50]
    assert fake.added[0][0] == "spotify:track:id0"


def yt_track(vid, title, artist, secs=200, **extra):
    return {"videoId": vid, "title": title, "artists": [{"name": artist, "id": "a"}],
            "album": {"name": "Alb"}, "duration_seconds": secs, **extra}


class FakeYT:
    def __init__(self):
        self.added = []

    def get_library_playlists(self, limit):
        return [{"playlistId": "LM", "title": "Liked Music"}, {"playlistId": "PL1", "title": "Gym", "count": "1,204"}]

    def get_playlist(self, pid, limit):
        return {"tracks": [yt_track("v1", "One", "A"), yt_track(None, "Gone", "X"), yt_track("v2", "Dead", "Y", isAvailable=False)]}

    def get_liked_songs(self, limit):
        return {"tracks": [yt_track("lv", "Liked", "A")]}

    def search(self, q, filter, limit):
        return [yt_track("s1", "One", "A")] if filter == "videos" else []

    def create_playlist(self, title, description, privacy_status):
        assert privacy_status == "PRIVATE" and "<" not in description
        return "PLnew"

    def add_playlist_items(self, pid, ids, duplicates):
        self.added.append(ids)


def test_ytmusic_adapter():
    fake = FakeYT()
    yt = YTMusicProvider(client=fake)
    assert [(p.id, p.count) for p in yt.list_playlists()] == [("PL1", 1204)]
    assert [t.id for t in yt.get_tracks("PL1")] == ["v1"]
    assert yt.get_tracks(LIKED)[0].id == "lv"
    assert [t.id for t in yt.search(Track("x", "One", ("A",)))] == ["s1"]  # falls back to videos
    assert yt.create_playlist("N", "a <b>", [f"v{i}" for i in range(101)]) == "PLnew"
    assert [len(b) for b in fake.added] == [100, 1]
