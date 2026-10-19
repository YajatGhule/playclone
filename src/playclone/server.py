"""Local web UI: a small JSON API plus a single-page front end, served on 127.0.0.1."""

from __future__ import annotations

import json
import secrets
import threading
import traceback
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .clone import MatchCache, match_tracks
from .config import config_dir, load_settings, save_settings
from .models import LIKED, Match, Track
from .providers import NAMES

PRETTY = {"spotify": "Spotify", "ytmusic": "YouTube Music"}


def playlist_url(service: str, pid: str) -> str:
    if service == "spotify":
        return f"https://open.spotify.com/playlist/{pid}"
    return f"https://music.youtube.com/playlist?list={pid}"


def _track_json(t: Track | None) -> dict | None:
    return asdict(t) if t else None


def _track_from(d: dict) -> Track:
    return Track(d["id"], d["title"], tuple(d.get("artists") or ()), d.get("album"), d.get("duration_s"), d.get("isrc"))


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class App:
    """Holds sign-in state, provider clients and clone jobs for the running server."""

    def __init__(self, port: int):
        self.port = port
        self.providers: dict[str, object] = {}
        self.jobs: dict[str, Job] = {}
        self.spotify_auth = None
        self.oauth_state: str | None = None
        self.lock = threading.Lock()

    # --- accounts ---------------------------------------------------------

    def redirect_uri(self) -> str:
        return load_settings().get("spotify_redirect_uri") or f"http://127.0.0.1:{self.port}/callback"

    def _spotify_auth(self):
        from .providers.spotify import make_auth

        if not load_settings().get("spotify_client_id"):
            return None
        if self.spotify_auth is None:
            self.spotify_auth = make_auth(open_browser=False, redirect_uri=self.redirect_uri())
        return self.spotify_auth

    def status(self) -> dict:
        settings = load_settings()
        sp = {"client_id": settings.get("spotify_client_id", ""), "signed_in": False, "user": None,
              "redirect_uri": self.redirect_uri()}
        auth = self._spotify_auth()
        if auth and auth.validate_token(auth.cache_handler.get_cached_token()):
            sp["signed_in"] = True
            try:
                me = self.provider("spotify").sp.current_user()
                sp["user"] = me.get("display_name") or me.get("id")
            except Exception as e:  # token revoked, app not allowlisted, offline...
                sp["signed_in"], sp["error"] = False, str(e)

        from .providers.ytmusic import auth_file

        yt = {"signed_in": auth_file().exists()}
        return {"mode": "local", "spotify": sp, "ytmusic": yt, "pending": []}

    def set_client_id(self, client_id: str) -> None:
        client_id = client_id.strip()
        if not client_id.isalnum():
            raise ApiError("That doesn't look like a Spotify client id.")
        save_settings(spotify_client_id=client_id)
        self.spotify_auth = None
        self.providers.pop("spotify", None)

    def spotify_login_url(self) -> str:
        auth = self._spotify_auth()
        if not auth:
            raise ApiError("Save your Spotify client id first.")
        self.oauth_state = secrets.token_urlsafe(16)
        auth.get_pkce_handshake_parameters()
        return auth.get_authorize_url(state=self.oauth_state)

    def spotify_callback(self, query: dict) -> None:
        if query.get("error"):
            raise ApiError(f"Spotify sign-in was cancelled ({query['error'][0]}).")
        if not self.oauth_state or query.get("state", [None])[0] != self.oauth_state:
            raise ApiError("Spotify sign-in state mismatch; please try again.")
        self.oauth_state = None
        self._spotify_auth().get_access_token(code=query["code"][0], check_cache=False)
        self.providers.pop("spotify", None)

    def ytmusic_login(self, headers_raw: str) -> int:
        import ytmusicapi

        from .providers.ytmusic import YTMusicProvider, auth_file

        if "cookie" not in headers_raw.lower():
            raise ApiError("Those headers have no Cookie line. Copy the request headers of a POST request "
                           "to music.youtube.com while signed in.")
        path = auth_file()
        try:
            ytmusicapi.setup(filepath=str(path), headers_raw=headers_raw)
            path.chmod(0o600)
            self.providers.pop("ytmusic", None)
            return len(self.provider("ytmusic").list_playlists())
        except Exception as e:
            path.unlink(missing_ok=True)
            self.providers.pop("ytmusic", None)
            raise ApiError(f"YouTube Music rejected those headers: {e}") from e

    def logout(self, service: str) -> None:
        if service == "spotify":
            (config_dir() / "spotify-token.json").unlink(missing_ok=True)
            self.spotify_auth = None
        else:
            from .providers.ytmusic import auth_file

            auth_file().unlink(missing_ok=True)
        self.providers.pop(service, None)

    def provider(self, service: str):
        if service not in NAMES:
            raise ApiError(f"Unknown service {service!r}")
        with self.lock:
            if service not in self.providers:
                if service == "spotify":
                    from .providers.spotify import SpotifyProvider

                    auth = self._spotify_auth()
                    if not auth or not auth.validate_token(auth.cache_handler.get_cached_token()):
                        raise ApiError("Connect Spotify first.", 401)
                    import spotipy

                    self.providers[service] = SpotifyProvider(
                        client=spotipy.Spotify(auth_manager=auth, retries=5, status_retries=5))
                else:
                    from .providers.ytmusic import YTMusicProvider, auth_file

                    if not auth_file().exists():
                        raise ApiError("Connect YouTube Music first.", 401)
                    self.providers[service] = YTMusicProvider()
            return self.providers[service]

    # --- library ----------------------------------------------------------

    def playlists(self, service: str) -> list[dict]:
        items = [{"id": LIKED, "name": "Liked songs", "count": None, "liked": True}]
        items += [{"id": p.id, "name": p.name, "count": p.count, "description": p.description}
                  for p in self.provider(service).list_playlists()]
        return items

    def search(self, service: str, q: str) -> list[dict]:
        q = q.strip()
        if not q:
            return []
        return [_track_json(t) for t in self.provider(service).search(Track("", q, ()))]

    # --- jobs -------------------------------------------------------------

    def start_job(self, body: dict) -> Job:
        src, dst = body.get("src"), body.get("dst")
        if src == dst or src not in NAMES or dst not in NAMES:
            raise ApiError("Pick two different services.")
        picks = body.get("playlists") or []
        if not picks:
            raise ApiError("Select at least one playlist.")
        job = Job(self, src, dst, picks, float(body.get("threshold", 0.75)))
        self.jobs[job.id] = job
        threading.Thread(target=job.run_matching, daemon=True).start()
        return job

    def job(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise ApiError("Unknown job", 404)
        return self.jobs[job_id]


class Job:
    def __init__(self, app: App, src: str, dst: str, picks: list[dict], threshold: float):
        self.id = secrets.token_hex(6)
        self.app, self.src, self.dst, self.threshold = app, src, dst, threshold
        self.status = "matching"  # matching -> review -> creating -> done | error
        self.error: str | None = None
        self.cache = MatchCache(config_dir() / "match-cache.json")
        self.lists = []
        for p in picks:
            liked = p["id"] == LIKED
            self.lists.append({
                "id": p["id"],
                "name": p["name"],
                "new_name": f"Liked songs (from {PRETTY[src]})" if liked else p["name"],
                "description": p.get("description") or f"Cloned from {PRETTY[src]} with playclone",
                "state": "waiting", "total": None, "done": 0, "matches": [],
                "playlist_id": None, "url": None, "error": None,
            })

    def run_matching(self) -> None:
        try:
            src, dst = self.app.provider(self.src), self.app.provider(self.dst)
            for pl in self.lists:
                pl["state"] = "loading"
                tracks = src.get_tracks(pl["id"])
                pl["total"], pl["state"] = len(tracks), "matching"

                def progress(i: int, total: int, m: Match, pl=pl) -> None:
                    pl["done"] = i
                    pl["matches"].append({"source": _track_json(m.source), "target": _track_json(m.target),
                                          "score": m.score})

                match_tracks(tracks, src, dst, self.cache, self.threshold, progress)
                pl["state"] = "matched"
            self.status = "review"
        except Exception as e:
            traceback.print_exc()
            self.status, self.error = "error", str(e)

    def create(self, body: dict) -> None:
        if self.status != "review":
            raise ApiError(f"Job is {self.status}, not ready to create.")
        plans = {p["index"]: p for p in body.get("playlists", [])}
        for pick in body.get("manual", []):  # remember hand-picked matches for next time
            key = MatchCache.key(self.src, self.dst, _track_from(pick["source"]))
            self.cache.put(key, _track_from(pick["target"]), 1.0)
        self.cache.save()
        self.status = "creating"
        threading.Thread(target=self._create, args=(plans,), daemon=True).start()

    def _create(self, plans: dict) -> None:
        dst = self.app.provider(self.dst)
        for i, pl in enumerate(self.lists):
            plan = plans.get(i)
            if not plan:
                pl["state"] = "skipped"
                continue
            ids = list(dict.fromkeys(plan.get("track_ids") or []))
            name = (plan.get("name") or pl["new_name"]).strip() or pl["new_name"]
            pl["state"] = "creating"
            try:
                pl["playlist_id"] = dst.create_playlist(name, pl["description"], ids)
                pl["url"], pl["new_name"], pl["created_count"] = playlist_url(self.dst, pl["playlist_id"]), name, len(ids)
                pl["state"] = "created"
            except Exception as e:
                traceback.print_exc()
                pl["state"], pl["error"] = "error", str(e)
        self.status = "done"

    def to_json(self) -> dict:
        return {"id": self.id, "src": self.src, "dst": self.dst, "status": self.status,
                "error": self.error, "playlists": self.lists}


def make_handler(app: App):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "playclone"

        def log_message(self, fmt, *args):  # quiet: only log errors
            if args and str(args[1]).startswith(("4", "5")):
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _redirect(self, location: str) -> None:
            self._send(HTTPStatus.FOUND, b"", "text/plain", {"Location": location})

        def _guard(self, write: bool) -> bool:
            # Block DNS-rebinding (Host) and cross-site requests (Origin / content type) from other web pages.
            if self.headers.get("Host") not in allowed_hosts:
                self._send(403, b"forbidden host", "text/plain")
                return False
            if write:
                origin = self.headers.get("Origin")
                if origin and urlparse(origin).netloc not in allowed_hosts:
                    self._send(403, b"forbidden origin", "text/plain")
                    return False
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    self._send(415, b"json only", "text/plain")
                    return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _dispatch(self, fn) -> None:
            try:
                fn()
            except ApiError as e:
                self._json({"error": str(e)}, e.status)
            except Exception as e:
                traceback.print_exc()
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_GET(self):
            if not self._guard(write=False):
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            path = url.path

            def route():
                if path == "/":
                    page = resources.files("playclone").joinpath("web/index.html").read_bytes()
                    self._send(200, page, "text/html; charset=utf-8",
                               {"Content-Security-Policy": "default-src 'self'; img-src 'self' data: https:; "
                                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
                elif path == "/backend.js":
                    js = resources.files("playclone").joinpath("web/backend-local.js").read_bytes()
                    self._send(200, js, "text/javascript; charset=utf-8")
                elif path == "/api/status":
                    self._json(app.status())
                elif path == "/api/spotify/login":
                    self._redirect(app.spotify_login_url())
                elif path == "/callback":
                    try:
                        app.spotify_callback(q)
                        self._redirect("/?connected=spotify")
                    except ApiError as e:
                        from urllib.parse import quote

                        self._redirect("/?error=" + quote(str(e)))
                elif path == "/api/playlists":
                    self._json(app.playlists(q.get("service", [""])[0]))
                elif path == "/api/search":
                    self._json(app.search(q.get("service", [""])[0], q.get("q", [""])[0]))
                elif path.startswith("/api/jobs/"):
                    self._json(app.job(path.rsplit("/", 1)[1]).to_json())
                else:
                    self._send(404, b"not found", "text/plain")

            self._dispatch(route)

        def do_POST(self):
            if not self._guard(write=True):
                return
            path = urlparse(self.path).path

            def route():
                body = self._body()
                if path == "/api/spotify/client-id":
                    app.set_client_id(body.get("client_id", ""))
                    self._json(app.status())
                elif path == "/api/ytmusic/login":
                    n = app.ytmusic_login(body.get("headers", ""))
                    self._json({"playlists": n})
                elif path == "/api/logout":
                    app.logout(body.get("service", ""))
                    self._json(app.status())
                elif path == "/api/jobs":
                    self._json(app.start_job(body).to_json())
                elif path.startswith("/api/jobs/") and path.endswith("/create"):
                    job = app.job(path.split("/")[3])
                    job.create(body)
                    self._json(job.to_json())
                else:
                    self._send(404, b"not found", "text/plain")

            self._dispatch(route)

    return Handler


def serve(port: int = 8888, open_browser: bool = True) -> None:
    app = App(port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"playclone is running at {url}  (Ctrl-C to quit)")
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
