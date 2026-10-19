# playclone

Clone playlists and liked songs between **Spotify** and **YouTube Music**, in either direction.

```
$ playclone clone --from spotify --to ytmusic "Gym" --liked

Gym → YouTube Music as 'Gym'
  [1/48] ✓ Queen - Bohemian Rhapsody - Remastered 2011  →  Queen - Bohemian Rhapsody
  [2/48] ✓ The Weeknd - Blinding Lights
  [3/48] ✗ Some Artist - Rare Demo  (0.41)
  ...
Gym: 46/48 matched, created PLxxxxxxxx
Unmatched tracks written to unmatched.tsv
```

Each source track is searched on the other service and scored on title, artist and duration (and ISRC codes when both sides have them, as Apple Music will). Junk like `(Official Video)`, `- Remastered 2011` and `feat. X` is ignored, and live, remix, acoustic and similar versions are kept apart. Tracks scoring below the threshold are skipped and listed in `unmatched.tsv` instead of being replaced with a wrong song. New playlists are always created **private**. Nothing is ever deleted or modified on either account.

## Install

```sh
git clone https://github.com/YajatGhule/playclone && cd playclone
uv sync            # or: pip install .
uv run playclone --help
```

Requires Python 3.12+.

## Sign in

### Spotify

1. Create an app at <https://developer.spotify.com/dashboard>. Enable **Web API**, and add the redirect URI
   `http://127.0.0.1:8888/callback`.
2. While the app is in development mode, add your Spotify account's email under **User Management**.
3. Copy the app's **Client ID**. No secret is needed, because playclone uses the PKCE flow.

```sh
uv run playclone auth spotify --client-id <CLIENT_ID>
```

A browser opens for you to approve access. The token is cached and refreshes automatically.

### YouTube Music

YouTube Music has no official library API, so playclone uses [ytmusicapi](https://github.com/sigma67/ytmusicapi)
with your browser session's headers:

```sh
uv run playclone auth ytmusic
```

1. Open <https://music.youtube.com> while signed in, then open DevTools → Network and filter for `browse`.
2. Click any POST request and copy its **request headers**. In Firefox use the "Raw" toggle; in Chrome right-click → Copy → Copy request headers.
3. Paste them into the prompt, then press Ctrl‑D on an empty line.

The session stays valid until you sign out of YouTube in that browser.

## Use

```sh
playclone list spotify                                   # playlist ids and names
playclone clone --from spotify --to ytmusic "Road Trip"  # by name (or id)
playclone clone --from ytmusic --to spotify --liked      # liked songs → new playlist
playclone clone --from spotify --to ytmusic --all        # everything
playclone clone --from spotify --to ytmusic Gym --dry-run   # preview matches only
playclone clone --from spotify --to ytmusic Gym --name "Gym (YT)" --threshold 0.8
```

Liked songs are cloned into a regular playlist named `Liked songs (from …)`. Matches are cached in
`~/.config/playclone/match-cache.json`, so re-running a clone or cloning overlapping playlists
skips searches it has already done (`--no-cache` disables this).

All credentials live in `~/.config/playclone/` with mode 600. Set `PLAYCLONE_HOME` to use a
different directory.

## Development

```sh
uv run pytest
```

Each service is an adapter in `src/playclone/providers/` implementing four methods: `list_playlists`,
`get_tracks`, `search` and `create_playlist`. Apple Music is planned as the next adapter.

## Notes

- The YouTube Music integration uses an unofficial API and may break when YouTube changes its web client. If it does,
  update `ytmusicapi` first.
- Spotify apps in development mode only work for accounts listed under User Management.
