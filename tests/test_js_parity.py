"""The browser build (site/backend.js) must score matches the same way as matching.py."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from playclone.matching import normalize_title, score
from playclone.models import Track

ROOT = Path(__file__).parent.parent

PAIRS = [
    (("Bohemian Rhapsody - Remastered 2011", ["Queen"], 354), ("Bohemian Rhapsody", ["Queen"], 355)),
    (("Blinding Lights", ["The Weeknd"], 200), ("Blinding Lights (Official Video)", ["The Weeknd"], 263)),
    (("Levitating (feat. DaBaby)", ["Dua Lipa", "DaBaby"], 203), ("Levitating", ["Dua Lipa"], 204)),
    (("Hey Jude", ["The Beatles"], 431), ("Hey Jude (Live)", ["The Beatles"], 431)),
    (("Hello", ["Adele"], 295), ("Hello", ["Lionel Richie"], 246)),
    (("Tum Hi Ho", ["Arijit Singh"], 262), ("Tum Hi Ho", ["Arijit Singh", "Mithoon"], 262)),
    (("Smells Like Teen Spirit", ["Nirvana"], 301), ("Come As You Are", ["Nirvana"], 219)),
    (("Café del Mar", ["Energy 52"], None), ("Cafe Del Mar (Original Mix)", ["Energy 52"], 470)),
    (("Despacito", ["Luis Fonsi"], 229), ("Despacito - Remix", ["Luis Fonsi", "Justin Bieber"], 229)),
    (("Shape of You", ["Ed Sheeran"], 233), ("Shape of You [Official Lyric Video]", ["Ed Sheeran - Topic"], 235)),
]
TITLES = ["Bohemian Rhapsody - Remastered 2011", "Stay (feat. Justin Bieber)", "Song [HD]", "A - B - C",
          "Track (Radio Edit)", "Hey Jude (Live)", "Ünïcödé (Official Audio)", "Peaches ft. Daniel Caesar"]

SCRIPT = """
import { M } from "./tests/js/load.mjs";
const data = JSON.parse(process.argv[2]);
const t = ([title, artists, d]) => ({ id: "x", title, artists, duration_s: d, isrc: null });
console.log(JSON.stringify({
  scores: data.pairs.map(([a, b]) => M.score(t(a), t(b))),
  titles: data.titles.map(M.normalizeTitle),
}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_matcher_agrees_with_python(tmp_path):
    script = ROOT / "tests" / "js" / "_parity.mjs"
    script.write_text(SCRIPT.replace("./tests/js/load.mjs", "./load.mjs"))
    try:
        out = subprocess.run(["node", str(script), json.dumps({"pairs": PAIRS, "titles": TITLES})],
                             capture_output=True, text=True, check=True, cwd=ROOT).stdout
    finally:
        script.unlink()
    js = json.loads(out)
    py_scores = [score(Track("x", a[0], tuple(a[1]), duration_s=a[2]), Track("x", b[0], tuple(b[1]), duration_s=b[2]))
                 for a, b in PAIRS]
    assert js["titles"] == [normalize_title(t) for t in TITLES]
    for (a, b), p, j in zip(PAIRS, py_scores, js["scores"]):
        assert abs(p - j) < 0.005, f"{a[0]!r} vs {b[0]!r}: python {p} js {j}"
