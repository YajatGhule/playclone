"""Score how likely a search result is the same recording as a source track."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .models import Track

# Bracketed/suffix noise that differs between services but not between recordings.
_NOISE = (
    r"official( music)?( video| audio| lyric video| visualizer)?|music video|lyric(s)?( video)?"
    r"|audio|video|visuali[sz]er|hd|hq|4k|explicit|clean|radio edit|single version"
    r"|(\d{4} )?remaster(ed)?( \d{4})?( version)?|feat\.? .*|ft\.? .*|with .*|prod\.? .*"
)
_BRACKETS = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+(.*)$")
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$")
_NOISE_RE = re.compile(rf"^\s*({_NOISE})\s*$")

# Words that mean a *different* recording if only one side has them.
_VARIANTS = ("live", "remix", "acoustic", "instrumental", "karaoke", "cover", "sped up", "slowed", "nightcore")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def normalize_title(title: str) -> str:
    t = _fold(title)
    t = _BRACKETS.sub(lambda m: " " if _NOISE_RE.match(m.group(1)) else f" {m.group(1)} ", t)
    m = _DASH_SUFFIX.search(t)
    if m and _NOISE_RE.match(m.group(1)):
        t = t[: m.start()]
    t = _FEAT.sub("", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def normalize_artist(artist: str) -> str:
    a = _fold(artist)
    a = re.sub(r"\s+-\s+topic$", "", a)  # YouTube "Artist - Topic" channels
    a = re.sub(r"[^\w\s]", " ", a)
    return re.sub(r"^the\s+", "", " ".join(a.split()))


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _variants(s: str) -> set[str]:
    return {v for v in _VARIANTS if re.search(rf"\b{v}\b", s)}


def score(src: Track, cand: Track) -> float:
    if src.isrc and cand.isrc and src.isrc.upper() == cand.isrc.upper():
        return 1.0

    st, ct = normalize_title(src.title), normalize_title(cand.title)
    title = _ratio(st, ct)
    if st and ct and (st in ct or ct in st):
        title = max(title, 0.85)

    sa = [normalize_artist(a) for a in src.artists]
    ca = [normalize_artist(a) for a in cand.artists]
    if sa and ca:
        artist = max(_ratio(x, y) for x in sa for y in ca)
    else:
        artist = 0.5

    if src.duration_s and cand.duration_s:
        diff = abs(src.duration_s - cand.duration_s)
        duration = 1.0 if diff <= 3 else 0.7 if diff <= 8 else 0.3 if diff <= 20 else 0.0
    else:
        duration = 0.6

    s = 0.5 * title + 0.3 * artist + 0.2 * duration
    if _variants(_fold(src.title)) != _variants(_fold(cand.title)):
        s -= 0.25
    return round(max(0.0, min(1.0, s)), 3)


def best_match(src: Track, candidates: list[Track], threshold: float) -> tuple[Track | None, float]:
    best, best_score = None, 0.0
    for c in candidates:
        s = score(src, c)
        if s > best_score:
            best, best_score = c, s
    return (best, best_score) if best_score >= threshold else (None, best_score)
