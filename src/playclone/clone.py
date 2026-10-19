from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .matching import best_match
from .models import CloneResult, Match, Track
from .providers.base import Provider


class MatchCache:
    """Remembers source→target matches so re-runs don't search again."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text())
            except json.JSONDecodeError:
                self.data = {}

    @staticmethod
    def key(src: str, dst: str, track: Track) -> str:
        return f"{src}>{dst}:{track.id}"

    def get(self, key: str) -> tuple[Track, float] | None:
        hit = self.data.get(key)
        if not hit:
            return None
        t = Track(hit["id"], hit["title"], tuple(hit["artists"]), duration_s=hit.get("duration_s"))
        return t, hit["score"]

    def put(self, key: str, track: Track, score: float) -> None:
        self.data[key] = {
            "id": track.id, "title": track.title, "artists": list(track.artists),
            "duration_s": track.duration_s, "score": score,
        }

    def save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))


def match_tracks(
    tracks: list[Track],
    src: Provider,
    dst: Provider,
    cache: MatchCache,
    threshold: float = 0.75,
    on_progress: Callable[[int, int, Match], None] | None = None,
) -> list[Match]:
    matches = []
    for i, track in enumerate(tracks, 1):
        key = MatchCache.key(src.name, dst.name, track)
        cached = cache.get(key)
        if cached:
            m = Match(track, *cached)
        else:
            target, score = best_match(track, dst.search(track), threshold)
            m = Match(track, target, score)
            if target:
                cache.put(key, target, score)
        matches.append(m)
        if on_progress:
            on_progress(i, len(tracks), m)
    cache.save()
    return matches


def clone_playlist(
    src: Provider,
    dst: Provider,
    playlist_id: str,
    name: str,
    description: str = "",
    *,
    cache: MatchCache,
    threshold: float = 0.75,
    dry_run: bool = False,
    on_progress: Callable[[int, int, Match], None] | None = None,
) -> CloneResult:
    tracks = src.get_tracks(playlist_id)
    result = CloneResult(name, match_tracks(tracks, src, dst, cache, threshold, on_progress))
    if not dry_run:
        ids = list(dict.fromkeys(m.target.id for m in result.matched))  # dedupe, keep order
        result.playlist_id = dst.create_playlist(name, description, ids)
    return result
