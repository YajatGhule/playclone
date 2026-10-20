import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from playclone.models import LIKED, Track
from playclone.server import App, make_handler
from tests.test_clone import CATALOG, SRC_TRACKS, FakeProvider


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYCLONE_HOME", str(tmp_path))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = App(port)
    app.providers["spotify"] = FakeProvider("spotify", {"p": SRC_TRACKS, LIKED: SRC_TRACKS[:1]})
    app.providers["ytmusic"] = FakeProvider("ytmusic", catalog=CATALOG)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def call(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=h | (headers or {}), method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, json.loads(raw) if r.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_for(base, job_id, status):
    for _ in range(100):
        code, job = call(f"{base}/api/jobs/{job_id}")
        if job["status"] == status:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job stuck at {job['status']}")


def test_serves_page(server):
    _, base = server
    code, body = call(base + "/")
    assert code == 200 and b"<title>playclone</title>" in body and b'src="backend.js"' in body
    code, js = call(base + "/backend.js")
    assert code == 200 and b'mode: "local"' in js


def test_rejects_foreign_host_origin_and_form_posts(server):
    _, base = server
    assert call(base + "/api/status", headers={"Host": "evil.example"})[0] == 403
    assert call(base + "/api/jobs", {}, headers={"Origin": "https://evil.example"})[0] == 403
    req = urllib.request.Request(base + "/api/logout", data=b"service=spotify", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 415


def test_playlists_include_liked(server):
    _, base = server
    code, lists = call(base + "/api/playlists?service=spotify")
    assert code == 200
    assert lists[0]["id"] == LIKED and [p["id"] for p in lists[1:]] == ["p"]


def test_full_clone_flow(server):
    app, base = server
    code, job = call(base + "/api/jobs", {"src": "spotify", "dst": "ytmusic", "threshold": 0.75,
                                          "playlists": [{"id": "p", "name": "Mix"}, {"id": LIKED, "name": "Liked songs"}]})
    assert code == 200
    job = wait_for(base, job["id"], "review")
    mix, liked = job["playlists"]
    assert mix["total"] == 4 and [m["target"]["id"] if m["target"] else None for m in mix["matches"]] == ["y1", "y2", None, "y1"]
    assert liked["new_name"] == "Liked songs (from Spotify)"

    code, results = call(base + "/api/search?service=ytmusic&q=Levitating")
    assert code == 200 and results[0]["id"] == "y2"

    manual = {"source": mix["matches"][2]["source"], "target": results[0]}
    code, _ = call(f"{base}/api/jobs/{job['id']}/create", {
        "playlists": [{"index": 0, "name": "My Mix", "track_ids": ["y1", "y2", "y2"]}], "manual": [manual]})
    assert code == 200
    job = wait_for(base, job["id"], "done")
    assert job["playlists"][0]["state"] == "created"
    assert job["playlists"][0]["url"] == "https://music.youtube.com/playlist?list=new-1"
    assert job["playlists"][1]["state"] == "skipped"
    assert app.providers["ytmusic"].created[0][0] == "My Mix"
    assert app.providers["ytmusic"].created[0][2] == ["y1", "y2"]

    cache = json.loads((app.jobs[job["id"]].cache.path).read_text())
    assert cache["spotify>ytmusic:s3"]["id"] == "y2"  # hand-picked match remembered

    assert call(f"{base}/api/jobs/{job['id']}/create", {"playlists": []})[0] == 400  # can't create twice


def test_same_service_rejected(server):
    _, base = server
    code, body = call(base + "/api/jobs", {"src": "spotify", "dst": "spotify", "playlists": [{"id": "p", "name": "x"}]})
    assert code == 400


def test_spotify_callback_checks_state(server):
    _, base = server

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as e:
        opener.open(base + "/callback?code=abc&state=forged")
    assert e.value.code == 302 and "error=" in e.value.headers["Location"]


def test_track_roundtrip():
    from playclone.server import _track_from, _track_json

    t = Track("a", "b", ("c", "d"), "e", 10, "ISRC")
    assert _track_from(_track_json(t)) == t


def test_second_run_offers_earlier_copy_and_updates_it(server):
    app, base = server
    _, job = call(base + "/api/jobs", {"src": "spotify", "dst": "ytmusic", "playlists": [{"id": "p", "name": "Mix"}]})
    job = wait_for(base, job["id"], "review")
    assert job["playlists"][0]["previous_id"] is None
    call(f"{base}/api/jobs/{job['id']}/create", {"playlists": [{"index": 0, "name": "Mix", "track_ids": ["y1"]}]})
    job = wait_for(base, job["id"], "done")
    copy_id = job["playlists"][0]["playlist_id"]

    _, job2 = call(base + "/api/jobs", {"src": "spotify", "dst": "ytmusic", "playlists": [{"id": "p", "name": "Mix"}]})
    job2 = wait_for(base, job2["id"], "review")
    assert job2["playlists"][0]["previous_id"] == copy_id
    assert copy_id in [p["id"] for p in job2["dst_playlists"]]
    call(f"{base}/api/jobs/{job2['id']}/create",
         {"playlists": [{"index": 0, "name": "x", "track_ids": ["y1", "y2"], "target_id": copy_id}]})
    job2 = wait_for(base, job2["id"], "done")
    pl = job2["playlists"][0]
    assert pl["updated"] and (pl["created_count"], pl["already_there"]) == (1, 1)
    assert app.providers["ytmusic"].added == [(copy_id, ["y2"])]
    assert len(app.providers["ytmusic"].created) == 1


def test_stop_returns_partial_review(server):
    app, base = server
    slow = app.providers["ytmusic"]
    real = slow.search

    def search(track):
        time.sleep(0.15)
        return real(track)

    slow.search = search
    _, job = call(base + "/api/jobs", {"src": "spotify", "dst": "ytmusic", "playlists": [{"id": "p", "name": "Mix"}]})
    time.sleep(0.2)
    assert call(f"{base}/api/jobs/{job['id']}/stop", {})[0] == 200
    job = wait_for(base, job["id"], "review")
    pl = job["playlists"][0]
    assert 0 < len(pl["matches"]) < pl["total"]
    assert job["warning"].startswith("Stopped early")
