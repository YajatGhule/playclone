from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .clone import CloneMemory, MatchCache, clone_playlist
from .config import config_dir, save_settings
from .models import LIKED, CloneResult, Match, Playlist
from .providers import NAMES, get_provider

PRETTY = {"spotify": "Spotify", "ytmusic": "YouTube Music"}


def cmd_auth(args) -> None:
    if args.service == "spotify":
        if args.client_id:
            save_settings(spotify_client_id=args.client_id)
        if args.redirect_uri:
            save_settings(spotify_redirect_uri=args.redirect_uri)
        from .providers.spotify import SpotifyProvider, make_auth

        make_auth().get_access_token(check_cache=False)
        me = SpotifyProvider().sp.current_user()
        print(f"Signed in to Spotify as {me.get('display_name') or me['id']}")
    else:
        import ytmusicapi

        from .providers.ytmusic import YTMusicProvider, auth_file

        print(
            "Open https://music.youtube.com signed in, open DevTools > Network, filter for 'browse',\n"
            "click any POST request, and copy its request headers (Firefox: 'Raw' toggle;\n"
            "Chrome: right-click > Copy > Copy request headers). Paste below, then press\n"
            "Ctrl-D on an empty line.\n"
        )
        path = auth_file()
        ytmusicapi.setup(filepath=str(path))
        path.chmod(0o600)
        n = len(YTMusicProvider().list_playlists())
        print(f"Signed in to YouTube Music ({n} playlists found). Saved {path}")


def cmd_list(args) -> None:
    provider = get_provider(args.service)
    print(f"{LIKED:<40}  Liked songs")
    for p in provider.list_playlists():
        count = f"({p.count})" if p.count is not None else ""
        print(f"{p.id:<40}  {p.name} {count}")


def _resolve(selectors: list[str], playlists: list[Playlist]) -> list[Playlist]:
    by_id = {p.id: p for p in playlists}
    out = []
    for sel in selectors:
        if sel in by_id:
            out.append(by_id[sel])
            continue
        hits = [p for p in playlists if p.name.casefold() == sel.casefold()]
        if not hits:
            hits = [p for p in playlists if sel.casefold() in p.name.casefold()]
        if len(hits) != 1:
            names = ", ".join(repr(p.name) for p in hits) or "none"
            raise SystemExit(f"playlist {sel!r} matched {len(hits)} playlists ({names}); use its id from `playclone list`")
        out.append(hits[0])
    return out


def _progress(i: int, total: int, m: Match) -> None:
    mark = "✓" if m.target else "✗"
    line = f"  [{i}/{total}] {mark} {m.source.label}"
    if m.target and m.target.label.casefold() != m.source.label.casefold():
        line += f"  →  {m.target.label}"
    if m.score < 1:
        line += f"  ({m.score:.2f})"
    print(line, flush=True)


def _report(results: list[CloneResult], path: Path | None, dry_run: bool) -> None:
    print()
    lines = []
    for r in results:
        if dry_run:
            status = "would add new songs to the earlier copy" if r.updated else "would create"
        elif r.updated:
            status = f"added {r.added} new to {r.playlist_id} ({r.already_there} already there)"
        else:
            status = f"created {r.playlist_id}"
        print(f"{r.name}: {len(r.matched)}/{len(r.matches)} matched, {status}")
        for m in r.unmatched:
            lines.append(f"{r.name}\t{m.source.label}\tbest={m.score:.2f}")
    if lines and path:
        path.write_text("playlist\ttrack\tnote\n" + "\n".join(lines) + "\n")
        print(f"Unmatched tracks written to {path}")


def cmd_clone(args) -> None:
    if args.src == args.dst:
        raise SystemExit("--from and --to must be different services")
    src, dst = get_provider(args.src), get_provider(args.dst)
    playlists = src.list_playlists()
    targets: list[Playlist] = []
    if args.liked:
        targets.append(Playlist(LIKED, f"Liked songs (from {PRETTY[args.src]})"))
    targets += playlists if args.all else _resolve(args.playlists, playlists)
    if not targets:
        raise SystemExit("nothing to clone: name playlists, or pass --liked / --all")
    if args.name and len(targets) != 1:
        raise SystemExit("--name only works when cloning a single playlist")

    cache = MatchCache(None if args.no_cache else config_dir() / "match-cache.json")
    memory = CloneMemory(config_dir() / "clones.json")
    dst_ids = {p.id for p in dst.list_playlists()} if args.update else set()
    results = []
    for p in targets:
        name = args.name or p.name
        target = memory.get(args.src, p.id, args.dst) if args.update else None
        if target not in dst_ids:  # never cloned before, or the copy was deleted
            target = None
        print(f"\n{p.name} → {PRETTY[args.dst]} " + (f"(updating {target})" if target else f"as {name!r}"))
        desc = p.description or f"Cloned from {PRETTY[args.src]} with playclone"
        r = clone_playlist(
            src, dst, p.id, name, desc,
            cache=cache, threshold=args.threshold, dry_run=args.dry_run, on_progress=_progress, target_id=target,
        )
        if r.playlist_id:
            memory.put(args.src, p.id, args.dst, r.playlist_id)
        results.append(r)
    _report(results, Path(args.report) if args.report else None, args.dry_run)


def cmd_ui(args) -> None:
    from .server import serve

    serve(port=args.port, open_browser=not args.no_browser)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="playclone", description="Clone playlists between Spotify and YouTube Music.")
    sub = ap.add_subparsers(dest="cmd")

    u = sub.add_parser("ui", help="open the web app (default when no command is given)")
    u.add_argument("--port", type=int, default=8888, help="local port (Spotify redirect URI must match; default 8888)")
    u.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    u.set_defaults(func=cmd_ui)

    a = sub.add_parser("auth", help="sign in to a service")
    a.add_argument("service", choices=NAMES)
    a.add_argument("--client-id", help="Spotify app client id (saved for later runs)")
    a.add_argument("--redirect-uri", help="Spotify redirect URI (default http://127.0.0.1:8888/callback)")
    a.set_defaults(func=cmd_auth)

    ls = sub.add_parser("list", help="list your playlists on a service")
    ls.add_argument("service", choices=NAMES)
    ls.set_defaults(func=cmd_list)

    c = sub.add_parser("clone", help="copy playlists from one service to another")
    c.add_argument("playlists", nargs="*", help="playlist names or ids (see `playclone list`)")
    c.add_argument("--from", dest="src", required=True, choices=NAMES)
    c.add_argument("--to", dest="dst", required=True, choices=NAMES)
    c.add_argument("--liked", action="store_true", help="also clone liked songs into a new playlist")
    c.add_argument("--all", action="store_true", help="clone every playlist")
    c.add_argument("--name", help="name for the new playlist (single playlist only)")
    c.add_argument("--dry-run", action="store_true", help="match tracks but don't create anything")
    c.add_argument("--threshold", type=float, default=0.75, help="minimum match score 0-1 (default 0.75)")
    c.add_argument("--report", default="unmatched.tsv", help="where to write unmatched tracks ('' to skip)")
    c.add_argument("--update", action="store_true",
                   help="if a playlist was cloned before, add only its new songs to that copy instead of making a new one")
    c.add_argument("--no-cache", action="store_true", help="ignore and don't update the match cache")
    c.set_defaults(func=cmd_clone)
    return ap


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv or ["ui"])
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)
