from playclone import cli
from playclone.models import Track
from tests.test_clone import CATALOG, SRC_TRACKS, FakeProvider


def test_clone_update_reuses_earlier_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PLAYCLONE_HOME", str(tmp_path))
    src = FakeProvider("spotify", {"p": list(SRC_TRACKS)})
    dst = FakeProvider("ytmusic", catalog=CATALOG)
    monkeypatch.setattr(cli, "get_provider", lambda name: src if name == "spotify" else dst)
    args = ["clone", "--from", "spotify", "--to", "ytmusic", "p", "--report", ""]

    cli.main(args)
    assert len(dst.created) == 1 and "created new-1" in capsys.readouterr().out

    src.playlists["p"].append(Track("s5", "Starboy", ("The Weeknd",), duration_s=230))
    dst.catalog.append(Track("y5", "Starboy", ("The Weeknd",), duration_s=230))
    cli.main(args + ["--update"])
    out = capsys.readouterr().out
    assert len(dst.created) == 1
    assert dst.added == [("new-1", ["y5"])]
    assert "added 1 new to new-1 (2 already there)" in out

    cli.main(args)  # without --update, a fresh copy is made
    assert len(dst.created) == 2
