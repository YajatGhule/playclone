from __future__ import annotations

from dataclasses import dataclass, field

LIKED = "liked"  # pseudo playlist id for a service's liked songs


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artists: tuple[str, ...]
    album: str | None = None
    duration_s: int | None = None
    isrc: str | None = None

    @property
    def label(self) -> str:
        return f"{', '.join(self.artists) or '?'} - {self.title}"


@dataclass(frozen=True)
class Playlist:
    id: str
    name: str
    description: str = ""
    count: int | None = None


@dataclass
class Match:
    source: Track
    target: Track | None
    score: float


@dataclass
class CloneResult:
    name: str
    matches: list[Match] = field(default_factory=list)
    playlist_id: str | None = None

    @property
    def matched(self) -> list[Match]:
        return [m for m in self.matches if m.target]

    @property
    def unmatched(self) -> list[Match]:
        return [m for m in self.matches if not m.target]
