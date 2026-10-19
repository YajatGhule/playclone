# playclone

Copy playlists and liked songs between **Spotify** and **YouTube Music**, in either direction, from a
local web app.

![playclone review screen](docs/screenshot.png)

1. **Connect** both accounts.
2. **Pick** the playlists to copy (Liked songs included) and the direction.
3. **Review** the matches: untick anything you don't want, and use **Find** to search for songs
   playclone couldn't match by itself. Then hit **Create playlists**.

Each track is searched on the other service and scored on title, artist and duration (plus ISRC
codes when both sides have them). Noise like `(Official Video)`, `- Remastered 2011` and `feat. X`
is ignored, and live, remix, acoustic and similar versions are kept apart from the originals.
Uncertain matches are flagged for review, and songs it can't find are left out instead of being
replaced with the wrong song. New playlists are always **private**. Nothing on either account is
ever deleted or modified.

## Install and run

```sh
git clone https://github.com/YajatGhule/playclone && cd playclone
uv sync            # or: pip install .
uv run playclone   # opens http://127.0.0.1:8888 in your browser
```

Requires Python 3.12+. The app runs entirely on your computer. Credentials are kept in
`~/.config/playclone/` with mode 600 (set `PLAYCLONE_HOME` to use another folder).

## Connecting accounts

The app walks you through both of these.

**Spotify:** create a (free) app at <https://developer.spotify.com/dashboard>, enable **Web API**,
add the redirect URI `http://127.0.0.1:8888/callback`, and add your Spotify email under
**User Management**. Paste the app's **Client ID** into playclone and click **Connect Spotify**.
No client secret is needed, because sign-in uses PKCE.

**YouTube Music:** there's no official library API, so playclone uses
[ytmusicapi](https://github.com/sigma67/ytmusicapi) with your browser session. On
music.youtube.com, open DevTools → Network, filter for `browse`, click a POST request, copy its
request headers and paste them into playclone. They stay valid until you sign out of YouTube in that
browser.

## Command line

Everything the app does is also available as commands:

```sh
playclone ui [--port 8888] [--no-browser]                  # the web app (default)
playclone auth spotify --client-id <ID>                    # or: playclone auth ytmusic
playclone list spotify                                     # playlist ids and names
playclone clone --from spotify --to ytmusic "Road Trip"    # by name or id
playclone clone --from ytmusic --to spotify --liked        # liked songs → new playlist
playclone clone --from spotify --to ytmusic --all --dry-run
```

If you change `--port`, the Spotify redirect URI must use the same port.

Matches, including ones you pick by hand in the app, are cached in
`~/.config/playclone/match-cache.json`, so re-runs and overlapping playlists don't search again.

## Development

```sh
uv run pytest
```

- `src/playclone/providers/` has one adapter per service, each with four methods:
  `list_playlists`, `get_tracks`, `search` and `create_playlist`. Apple Music is planned as the next one.
- `src/playclone/server.py` is the local JSON API, built on the standard-library HTTP server.
- `src/playclone/web/index.html` is the whole front end, with no build step.

The server only listens on 127.0.0.1. It rejects requests with a foreign `Host` or `Origin` and
anything that isn't a JSON POST, so other websites can't drive it.

## Notes

- The YouTube Music integration uses an unofficial API and may break when YouTube changes its web
  client. If it does, update `ytmusicapi` first.
- Spotify apps in development mode only work for accounts listed under User Management.
