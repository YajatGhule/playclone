from playclone.clone import MatchCache, clone_playlist
from playclone.models import LIKED, Playlist, Track


class FakeProvider:
    def __init__(self, name, playlists=None, catalog=()):
        self.name = name
        self.playlists = playlists or {}
        self.catalog = list(catalog)
        self.created = []
        self.searches = 0

    def list_playlists(self):
        return [Playlist(pid, pid) for pid in self.playlists if pid != LIKED]

    def get_tracks(self, playlist_id):
        return self.playlists[playlist_id]

    def search(self, track):
        self.searches += 1
        return [t for t in self.catalog if t.title.split(" (")[0].lower() in track.title.lower()]

    def create_playlist(self, name, description, track_ids):
        self.created.append((name, description, track_ids))
        return f"new-{len(self.created)}"


SRC_TRACKS = [
    Track("s1", "Blinding Lights", ("The Weeknd",), duration_s=200),
    Track("s2", "Levitating", ("Dua Lipa",), duration_s=203),
    Track("s3", "Some Obscure Demo", ("Nobody",), duration_s=100),
    Track("s4", "Blinding Lights", ("The Weeknd",), duration_s=200),  # duplicate
]
CATALOG = [
    Track("y1", "Blinding Lights", ("The Weeknd",), duration_s=201),
    Track("y2", "Levitating (Official Video)", ("Dua Lipa",), duration_s=204),
]


def make():
    return FakeProvider("spotify", {"p": SRC_TRACKS}), FakeProvider("ytmusic", catalog=CATALOG)


def test_clone_creates_playlist_with_matches(tmp_path):
    src, dst = make()
    res = clone_playlist(src, dst, "p", "Mix", "desc", cache=MatchCache(tmp_path / "c.json"))
    assert [m.target.id if m.target else None for m in res.matches] == ["y1", "y2", None, "y1"]
    assert res.playlist_id == "new-1"
    assert dst.created == [("Mix", "desc", ["y1", "y2"])]  # deduped, order kept
    assert [m.source.id for m in res.unmatched] == ["s3"]


def test_dry_run_creates_nothing(tmp_path):
    src, dst = make()
    res = clone_playlist(src, dst, "p", "Mix", cache=MatchCache(tmp_path / "c.json"), dry_run=True)
    assert dst.created == [] and res.playlist_id is None and len(res.matched) == 3


def test_cache_skips_repeat_searches(tmp_path):
    path = tmp_path / "c.json"
    src, dst = make()
    clone_playlist(src, dst, "p", "Mix", cache=MatchCache(path), dry_run=True)
    first = dst.searches
    src, dst = make()
    clone_playlist(src, dst, "p", "Mix", cache=MatchCache(path), dry_run=True)
    assert dst.searches == 1  # only the unmatched track is searched again
    assert first == 4
