import pytest

from playclone.matching import best_match, normalize_artist, normalize_title, score
from playclone.models import Track


def T(title, *artists, d=None, isrc=None, id="x"):
    return Track(id, title, artists, duration_s=d, isrc=isrc)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Bohemian Rhapsody - Remastered 2011", "bohemian rhapsody"),
        ("Blinding Lights (Official Video)", "blinding lights"),
        ("Stay (feat. Justin Bieber)", "stay"),
        ("Peaches ft. Daniel Caesar", "peaches"),
        ("Despacito [Official Music Video]", "despacito"),
        ("Café del Mar", "cafe del mar"),
        ("Hey Jude (Live)", "hey jude live"),
        ("Song (Remix)", "song remix"),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


def test_normalize_artist():
    assert normalize_artist("The Weeknd") == "weeknd"
    assert normalize_artist("Daft Punk - Topic") == "daft punk"
    assert normalize_artist("Beyoncé") == "beyonce"


def test_isrc_is_exact():
    assert score(T("a", "b", isrc="USUM71703861"), T("zzz", "q", isrc="usum71703861")) == 1.0


def test_same_song_different_labels_matches():
    src = T("Bohemian Rhapsody - Remastered 2011", "Queen", d=354)
    cand = T("Bohemian Rhapsody", "Queen", d=355)
    assert score(src, cand) >= 0.95


def test_live_version_penalized():
    src = T("Hey Jude", "The Beatles", d=431)
    studio = T("Hey Jude", "The Beatles", d=429, id="studio")
    live = T("Hey Jude (Live)", "The Beatles", d=431, id="live")
    assert score(src, live) < 0.75
    assert best_match(src, [live, studio], 0.75)[0].id == "studio"


def test_wrong_artist_rejected():
    src = T("Hello", "Adele", d=295)
    assert best_match(src, [T("Hello", "Lionel Richie", d=246)], 0.75)[0] is None


def test_no_candidates():
    assert best_match(T("x", "y"), [], 0.75) == (None, 0.0)
