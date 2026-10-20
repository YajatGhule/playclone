"use strict";
// Backend for the GitHub Pages build: everything runs in the browser.
// Spotify via PKCE, YouTube via the official Data API with Google sign-in.
// It answers the same /api/* routes, with the same JSON, as the Python server.
(() => {
  const LIKED = "liked";
  const PRETTY = { spotify: "Spotify", ytmusic: "YouTube Music" };
  const YT_QUOTA = 10000;

  class ApiError extends Error {
    constructor(message, status = 400) { super(message); this.status = status; }
  }
  class QuotaError extends ApiError {}

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const store = {
    get(k, d = null) { try { const v = localStorage.getItem("pc:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("pc:" + k, JSON.stringify(v)); } catch {} },
    del(k) { try { localStorage.removeItem("pc:" + k); } catch {} },
  };
  const redirectUri = () => location.origin + location.pathname.replace(/index\.html$/, "");

  /* ---------- matching (port of matching.py) ---------- */
  const NOISE = String.raw`official( music)?( video| audio| lyric video| visualizer)?|music video|lyric(s)?( video)?` +
    String.raw`|audio|video|visuali[sz]er|hd|hq|4k|explicit|clean|radio edit|single version` +
    String.raw`|(\d{4} )?remaster(ed)?( \d{4})?( version)?|feat\.? .*|ft\.? .*|with .*|prod\.? .*`;
  const NOISE_RE = new RegExp(`^\\s*(${NOISE})\\s*$`, "u");
  const BRACKETS = /[\(\[]([^\)\]]*)[\)\]]/gu;
  const DASH_SUFFIX = /\s+-\s+(.*)$/u;
  const FEAT = /\s+(feat\.?|ft\.?|featuring)\s+.*$/u;
  const PUNCT = /[^\p{L}\p{N}\p{M}_\s]/gu;
  const VARIANTS = ["live", "remix", "acoustic", "instrumental", "karaoke", "cover", "sped up", "slowed", "nightcore"];

  const fold = s => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const squash = s => s.split(/\s+/u).filter(Boolean).join(" ");

  function normalizeTitle(title) {
    let t = fold(title || "");
    t = t.replace(BRACKETS, (_, inner) => NOISE_RE.test(inner) ? " " : ` ${inner} `);
    const m = DASH_SUFFIX.exec(t);
    if (m && NOISE_RE.test(m[1])) t = t.slice(0, m.index);
    t = t.replace(FEAT, "").replace(PUNCT, " ");
    return squash(t);
  }

  function normalizeArtist(artist) {
    let a = fold(artist || "").replace(/\s+-\s+topic$/u, "").replace(PUNCT, " ");
    return squash(a).replace(/^the\s+/u, "");
  }

  // Ratcliff/Obershelp, same as Python's difflib.SequenceMatcher.ratio() for short strings.
  function ratio(a, b) {
    if (!a || !b) return 0;
    let matched = 0;
    const stack = [[0, a.length, 0, b.length]];
    while (stack.length) {
      const [alo, ahi, blo, bhi] = stack.pop();
      let bi = alo, bj = blo, bk = 0, prev = new Map();
      for (let i = alo; i < ahi; i++) {
        const cur = new Map();
        for (let j = blo; j < bhi; j++) {
          if (a[i] !== b[j]) continue;
          const k = (prev.get(j - 1) || 0) + 1;
          cur.set(j, k);
          if (k > bk) { bi = i - k + 1; bj = j - k + 1; bk = k; }
        }
        prev = cur;
      }
      if (!bk) continue;
      matched += bk;
      if (alo < bi && blo < bj) stack.push([alo, bi, blo, bj]);
      if (bi + bk < ahi && bj + bk < bhi) stack.push([bi + bk, ahi, bj + bk, bhi]);
    }
    return 2 * matched / (a.length + b.length);
  }

  const variants = s => VARIANTS.filter(v => new RegExp(`(^|[^\\p{L}\\p{N}])${v}($|[^\\p{L}\\p{N}])`, "u").test(s)).join("|");

  function score(src, cand) {
    if (src.isrc && cand.isrc && src.isrc.toUpperCase() === cand.isrc.toUpperCase()) return 1;
    const st = normalizeTitle(src.title), ct = normalizeTitle(cand.title);
    let title = ratio(st, ct);
    if (st && ct && (st.includes(ct) || ct.includes(st))) title = Math.max(title, 0.85);

    const sa = (src.artists || []).map(normalizeArtist), ca = (cand.artists || []).map(normalizeArtist);
    let artist = 0.5;
    if (sa.length && ca.length) artist = Math.max(...sa.flatMap(x => ca.map(y => ratio(x, y))));

    let duration = 0.6;
    if (src.duration_s && cand.duration_s) {
      const diff = Math.abs(src.duration_s - cand.duration_s);
      duration = diff <= 3 ? 1 : diff <= 8 ? 0.7 : diff <= 20 ? 0.3 : 0;
    }
    let s = 0.5 * title + 0.3 * artist + 0.2 * duration;
    if (variants(fold(src.title)) !== variants(fold(cand.title))) s -= 0.25;
    return Math.round(Math.max(0, Math.min(1, s)) * 1000) / 1000;
  }

  function bestMatch(src, candidates, threshold) {
    let best = null, bestScore = 0;
    for (const c of candidates) {
      const s = score(src, c);
      if (s > bestScore) { best = c; bestScore = s; }
    }
    return bestScore >= threshold ? [best, bestScore] : [null, bestScore];
  }

  // Strip "(Official Video)", "- Remastered", "feat. X" from a raw title for use as a search query.
  function queryTitle(raw) {
    let t = (raw || "").replace(/[\(\[]([^\)\]]*)[\)\]]/gu, (all, inner) => NOISE_RE.test(fold(inner)) ? " " : all);
    const m = DASH_SUFFIX.exec(t);
    if (m && NOISE_RE.test(fold(m[1]))) t = t.slice(0, m.index);
    return squash(t.replace(/\s+(feat\.?|ft\.?|featuring)\s+.*$/iu, ""));
  }

  /* ---------- Spotify ---------- */
  const SP_SCOPES = "playlist-read-private playlist-read-collaborative playlist-modify-private user-library-read";
  const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const randomString = (n = 64) => { const a = new Uint8Array(n); crypto.getRandomValues(a); return b64url(a).slice(0, n); };

  async function spotifyLogin() {
    const clientId = store.get("spotify_client_id");
    if (!clientId) throw new ApiError("Save your Spotify client id first.");
    const verifier = randomString(64);
    const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    const state = randomString(22);
    store.set("sp_pkce", { verifier, state });
    const q = new URLSearchParams({
      client_id: clientId, response_type: "code", redirect_uri: redirectUri(),
      code_challenge_method: "S256", code_challenge: challenge, scope: SP_SCOPES, state,
    });
    location.href = "https://accounts.spotify.com/authorize?" + q;
  }

  async function spotifyToken(fields) {
    const r = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: store.get("spotify_client_id"), ...fields }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (fields.grant_type === "refresh_token") store.del("sp_token");
      throw new ApiError(`Spotify sign-in failed: ${d.error_description || d.error || r.status}`, 401);
    }
    const prev = store.get("sp_token") || {};
    const tok = { access_token: d.access_token, refresh_token: d.refresh_token || prev.refresh_token,
                  expires_at: Date.now() + (d.expires_in - 60) * 1000 };
    store.set("sp_token", tok);
    return tok;
  }

  async function spotifyCallback(params) {
    const saved = store.get("sp_pkce");
    store.del("sp_pkce");
    if (params.get("error")) throw new ApiError(`Spotify sign-in was cancelled (${params.get("error")}).`);
    if (!saved || saved.state !== params.get("state")) throw new ApiError("Spotify sign-in state mismatch; please try again.");
    await spotifyToken({ grant_type: "authorization_code", code: params.get("code"), redirect_uri: redirectUri(), code_verifier: saved.verifier });
  }

  async function spotifyAccess() {
    const t = store.get("sp_token");
    if (!t) throw new ApiError("Connect Spotify first.", 401);
    if (Date.now() < t.expires_at) return t.access_token;
    if (!t.refresh_token) { store.del("sp_token"); throw new ApiError("Spotify session expired. Connect Spotify again.", 401); }
    return (await spotifyToken({ grant_type: "refresh_token", refresh_token: t.refresh_token })).access_token;
  }

  async function spApi(path, { method = "GET", body, params } = {}) {
    const url = new URL(path.startsWith("https:") ? path : "https://api.spotify.com/v1/" + path);
    for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, v);
    for (let attempt = 0; ; attempt++) {
      const headers = { Authorization: "Bearer " + await spotifyAccess() };
      if (body) headers["Content-Type"] = "application/json";
      const r = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
      if (r.status === 429 && attempt < 6) { await sleep(Math.min(30, +r.headers.get("Retry-After") || 2) * 1000); continue; }
      if (r.status >= 500 && attempt < 3) { await sleep(800 * (attempt + 1)); continue; }
      if (r.status === 401 && attempt === 0) {
        const t = store.get("sp_token");
        if (t) { t.expires_at = 0; store.set("sp_token", t); }
        continue;
      }
      const d = r.status === 204 ? {} : await r.json().catch(() => ({}));
      if (!r.ok) {
        const msg = d.error?.message || r.statusText || r.status;
        const hint = r.status === 403 ? " (is your account added under User Management in the Spotify dashboard?)" : "";
        throw new ApiError(`Spotify: ${msg}${hint}`, r.status);
      }
      return d;
    }
  }

  function spTrack(t) {
    if (!t || (t.type && t.type !== "track") || t.is_local || !t.id) return null;
    return { id: t.id, title: t.name, artists: (t.artists || []).map(a => a.name), album: t.album?.name || null,
             duration_s: t.duration_ms ? Math.round(t.duration_ms / 1000) : null, isrc: t.external_ids?.isrc || null };
  }

  async function* spPages(page) {
    while (page) {
      yield* page.items || [];
      page = page.next ? await spApi(page.next) : null;
    }
  }

  const Spotify = {
    name: "spotify",
    async listPlaylists() {
      const out = [];
      for await (const p of spPages(await spApi("me/playlists", { params: { limit: 50 } }))) {
        if (p) out.push({ id: p.id, name: p.name, description: p.description || "", count: (p.items || p.tracks || {}).total ?? null });
      }
      return out;
    },
    async getTracks(id) {
      const first = id === LIKED
        ? await spApi("me/tracks", { params: { limit: 50 } })
        : await spApi(`playlists/${id}/items`, { params: { limit: 50, additional_types: "track" } });
      const out = [];
      for await (const item of spPages(first)) {
        const t = spTrack(item.track || item.item);
        if (t) out.push(t);
      }
      return out;
    },
    async search(track) {
      const clean = s => s.replace(/["':]/g, " ");
      const title = clean(queryTitle(track.title)), artist = track.artists?.[0] ? clean(track.artists[0]) : "";
      const queries = [];
      if (track.isrc) queries.push(`isrc:${track.isrc}`);
      queries.push(artist ? `track:${title} artist:${artist}` : `track:${title}`);
      queries.push(`${title} ${artist}`.trim());
      const seen = new Set(), out = [];
      for (const q of queries) {
        const d = await spApi("search", { params: { q, type: "track", limit: 10 } });
        for (const item of d.tracks?.items || []) {
          const t = spTrack(item);
          if (t && !seen.has(t.id)) { seen.add(t.id); out.push(t); }
        }
        if (out.some(c => score(track, c) >= 0.9)) break; // good enough, skip the looser queries
      }
      return out;
    },
    async createEmpty(name, description) {
      const pl = await spApi("me/playlists", { method: "POST", body: { name, public: false, description: description.slice(0, 300) } });
      return pl.id;
    },
    async addItems(pid, ids, onAdded) {
      for (let i = 0; i < ids.length; i += 100) {
        const chunk = ids.slice(i, i + 100);
        await spApi(`playlists/${pid}/items`, { method: "POST", body: { uris: chunk.map(id => `spotify:track:${id}`) } });
        onAdded(chunk.length);
      }
    },
    async listIds(pid) { return (await this.getTracks(pid)).map(t => t.id); },
    url: pid => `https://open.spotify.com/playlist/${pid}`,
  };

  /* ---------- YouTube (Data API v3) ---------- */
  const YT_SCOPE = "https://www.googleapis.com/auth/youtube";
  const ptDay = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles" }).format(new Date());
  const quota = () => { const q = store.get("yt_quota"); return q && q.day === ptDay() ? q : { day: ptDay(), used: 0 }; };
  const spend = units => { const q = quota(); q.used += units; store.set("yt_quota", q); };

  let gis = null;
  function loadGoogle() {
    if (window.google?.accounts?.oauth2) return Promise.resolve();
    return gis ||= new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true;
      s.onload = resolve;
      s.onerror = () => { gis = null; reject(new ApiError("Couldn't load Google sign-in. Check your connection or ad blocker.")); };
      document.head.appendChild(s);
    });
  }

  async function connectYouTube() {
    const clientId = store.get("google_client_id");
    if (!clientId) throw new ApiError("Save your Google client id first.");
    await loadGoogle(); // preloaded on page load, so the popup still counts as a user click
    return new Promise((resolve, reject) => {
      const client = google.accounts.oauth2.initTokenClient({
        client_id: clientId, scope: YT_SCOPE,
        callback: r => {
          if (r.error) return reject(new ApiError(`Google sign-in failed: ${r.error_description || r.error}`));
          store.set("yt_token", { access_token: r.access_token, expires_at: Date.now() + (Number(r.expires_in) - 60) * 1000 });
          resolve();
        },
        error_callback: e => reject(new ApiError(e?.type === "popup_closed" ? "The Google sign-in window was closed."
          : e?.type === "popup_failed_to_open" ? "Your browser blocked the Google sign-in popup. Allow popups for this site and try again."
          : `Google sign-in failed: ${e?.message || e?.type}`)),
      });
      client.requestAccessToken();
    });
  }

  const ytExpired = () => new ApiError("Your YouTube session expired (they last an hour). Click Connect on the YouTube Music card, then try again.", 401);

  async function ytApi(path, { method = "GET", params = {}, body, cost = 1 } = {}) {
    const t = store.get("yt_token");
    if (!t || Date.now() > t.expires_at) throw ytExpired();
    if (quota().used + cost > YT_QUOTA) throw new QuotaError("YouTube's daily API quota is used up. It resets at midnight Pacific time.", 429);
    const url = new URL("https://www.googleapis.com/youtube/v3/" + path);
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    for (let attempt = 0; ; attempt++) {
      const headers = { Authorization: "Bearer " + t.access_token };
      if (body) headers["Content-Type"] = "application/json";
      const r = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
      if (r.status >= 500 && attempt < 3) { await sleep(800 * (attempt + 1)); continue; }
      const d = r.status === 204 ? {} : await r.json().catch(() => ({}));
      const reason = d.error?.errors?.[0]?.reason || "";
      if (reason === "quotaExceeded" || reason === "dailyLimitExceeded") {
        store.set("yt_quota", { day: ptDay(), used: YT_QUOTA });
        throw new QuotaError("YouTube's daily API quota is used up. It resets at midnight Pacific time.", 429);
      }
      spend(cost);
      if (r.status === 401) { store.del("yt_token"); throw ytExpired(); }
      if (!r.ok) throw new ApiError(`YouTube: ${d.error?.message || r.statusText || r.status}`, r.status);
      return d;
    }
  }

  function isoDuration(s) {
    const m = /^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(s || "");
    if (!m) return null;
    return (+m[1] || 0) * 86400 + (+m[2] || 0) * 3600 + (+m[3] || 0) * 60 + (+m[4] || 0) || null;
  }

  // YouTube videos have no artist field: use the channel ("Queen - Topic", "TheWeekndVEVO"),
  // or split "Artist - Title" when the channel isn't an auto-generated Topic channel.
  function ytTrack(v) {
    const sn = v.snippet || {};
    let title = sn.title || "";
    const channel = sn.channelTitle || sn.videoOwnerChannelTitle || "";
    const topic = /\s-\sTopic$/i.test(channel);
    let artist = channel.replace(/\s-\sTopic$/i, "").replace(/\s*VEVO$/i, "").trim();
    if (!topic) {
      const m = /^(.+?)\s+[-–—]\s+(.+)$/u.exec(title);
      if (m) { artist = m[1].trim(); title = m[2].trim(); }
    }
    return { id: v.id, title, artists: artist ? [artist] : [], album: null,
             duration_s: isoDuration(v.contentDetails?.duration), isrc: null };
  }

  async function ytVideos(ids) {
    const out = new Map();
    for (let i = 0; i < ids.length; i += 50) {
      const d = await ytApi("videos", { params: { part: "snippet,contentDetails", id: ids.slice(i, i + 50).join(","), maxResults: 50 } });
      for (const v of d.items || []) out.set(v.id, v);
    }
    return out;
  }

  async function ytAll(path, params) {
    const items = [];
    let pageToken;
    do {
      const d = await ytApi(path, { params: { ...params, maxResults: 50, ...(pageToken ? { pageToken } : {}) } });
      items.push(...(d.items || []));
      pageToken = d.nextPageToken;
    } while (pageToken);
    return items;
  }

  const YouTube = {
    name: "ytmusic",
    async listPlaylists() {
      return (await ytAll("playlists", { part: "snippet,contentDetails", mine: "true" }))
        .map(p => ({ id: p.id, name: p.snippet.title, description: p.snippet.description || "", count: p.contentDetails?.itemCount ?? null }));
    },
    async getTracks(id) {
      if (id === LIKED) {
        // Liked songs on YouTube Music are liked videos; keep only the ones in the Music category.
        return (await ytAll("videos", { part: "snippet,contentDetails", myRating: "like" }))
          .filter(v => v.snippet?.categoryId === "10").map(ytTrack);
      }
      const items = await ytAll("playlistItems", { part: "contentDetails", playlistId: id });
      const ids = items.map(i => i.contentDetails?.videoId).filter(Boolean);
      const videos = await ytVideos(ids);
      return ids.map(v => videos.get(v)).filter(Boolean).map(ytTrack); // deleted/private videos drop out
    },
    async search(track) {
      const q = `${queryTitle(track.title)} ${track.artists?.[0] || ""}`.trim();
      const d = await ytApi("search", { cost: 100, params: { part: "snippet", type: "video", videoCategoryId: "10", maxResults: 10, q } });
      const ids = (d.items || []).map(i => i.id?.videoId).filter(Boolean);
      if (!ids.length) return [];
      const videos = await ytVideos(ids);
      return ids.map(id => videos.get(id)).filter(Boolean).map(ytTrack);
    },
    async createEmpty(name, description) {
      const d = await ytApi("playlists", { method: "POST", cost: 50, params: { part: "snippet,status" },
        body: { snippet: { title: name.slice(0, 150), description: description.replace(/[<>]/g, "").slice(0, 5000) },
                status: { privacyStatus: "private" } } });
      return d.id;
    },
    async addItems(pid, ids, onAdded) {
      for (const videoId of ids) {
        await ytApi("playlistItems", { method: "POST", cost: 50, params: { part: "snippet" },
          body: { snippet: { playlistId: pid, resourceId: { kind: "youtube#video", videoId } } } });
        onAdded(1);
      }
    },
    async listIds(pid) { // ~1 unit per 50 songs: skips the video-details lookup getTracks does
      return (await ytAll("playlistItems", { part: "contentDetails", playlistId: pid })).map(i => i.contentDetails?.videoId).filter(Boolean);
    },
    url: pid => `https://music.youtube.com/playlist?list=${pid}`,
  };

  const providers = { spotify: Spotify, ytmusic: YouTube };
  const provider = name => {
    if (!providers[name]) throw new ApiError(`Unknown service ${name}`);
    return providers[name];
  };

  /* ---------- match cache ---------- */
  const cacheKey = (src, dst, t) => `${src}>${dst}:${t.id}`;
  const cloneKey = (src, pid, dst) => `${src}:${pid}>${dst}`;
  let cache = null;
  const loadCache = () => cache ||= store.get("match-cache", {});
  const saveCache = () => store.set("match-cache", cache);

  /* ---------- jobs ---------- */
  const jobs = new Map();
  const snapshot = job => JSON.parse(JSON.stringify(job));
  const newId = () => randomString(12).replace(/[-_]/g, "x");

  function startJob(body) {
    const { src, dst } = body;
    if (src === dst || !providers[src] || !providers[dst]) throw new ApiError("Pick two different services.");
    const picks = body.playlists || [];
    if (!picks.length) throw new ApiError("Select at least one playlist.");
    const job = {
      id: newId(), src, dst, status: "matching", error: null, warning: null, threshold: +body.threshold || 0.75,
      stop_requested: false, dst_playlists: [],
      playlists: picks.map(p => ({
        id: p.id, name: p.name,
        new_name: p.id === LIKED ? `Liked songs (from ${PRETTY[src]})` : p.name,
        description: p.description || `Cloned from ${PRETTY[src]} with playclone`,
        state: "waiting", total: null, done: 0, matches: [], playlist_id: null, url: null, error: null,
        source_id: p.id, previous_id: null,
      })),
    };
    jobs.set(job.id, job);
    runMatching(job);
    return job;
  }

  async function runMatching(job) {
    const src = provider(job.src), dst = provider(job.dst), c = loadCache();
    let quotaHit = false, sinceSave = 0;
    try {
      job.dst_playlists = (await dst.listPlaylists()).map(p => ({ id: p.id, name: p.name }));
      const clones = store.get("clones", {});
      for (const pl of job.playlists) {
        const prev = clones[cloneKey(job.src, pl.id, job.dst)];
        pl.previous_id = job.dst_playlists.some(p => p.id === prev) ? prev : null;
      }
      for (const pl of job.playlists) {
        if (job.stop_requested) { pl.state = "stopped"; continue; }
        pl.state = "loading";
        const tracks = await src.getTracks(pl.id);
        pl.total = tracks.length;
        pl.state = "matching";
        for (const t of tracks) {
          if (job.stop_requested) break;
          const key = cacheKey(job.src, job.dst, t);
          let m;
          if (c[key]) {
            const { score: s, ...target } = c[key];
            m = { source: t, target, score: s };
          } else if (quotaHit) {
            m = { source: t, target: null, score: 0, skipped: true };
          } else {
            try {
              const [target, s] = bestMatch(t, await dst.search(t), job.threshold);
              m = { source: t, target, score: s };
              if (target) { c[key] = { ...target, score: s }; if (++sinceSave >= 10) { saveCache(); sinceSave = 0; } }
            } catch (e) {
              if (!(e instanceof QuotaError)) throw e;
              quotaHit = true;
              m = { source: t, target: null, score: 0, skipped: true };
            }
          }
          pl.matches.push(m);
          pl.done++;
        }
        pl.state = "matched";
      }
      if (job.stop_requested) {
        const left = job.playlists.reduce((n, pl) => n + (pl.total || 0) - pl.matches.length, 0);
        const skipped = job.playlists.filter(pl => pl.state === "stopped").length;
        const parts = [left && `${left} song${left === 1 ? "" : "s"}`, skipped && `${skipped} playlist${skipped === 1 ? "" : "s"}`].filter(Boolean);
        job.warning = `Stopped early: ${parts.join(" and ") || "nothing"} weren't checked and will be left out.`;
      }
      if (quotaHit) {
        const skipped = job.playlists.reduce((n, pl) => n + pl.matches.filter(m => m.skipped).length, 0);
        job.warning = `YouTube's daily API quota ran out, so ${skipped} song${skipped === 1 ? " wasn't" : "s weren't"} searched. ` +
          "Matches found so far are saved: run this again after midnight Pacific time to pick up the rest.";
      }
      job.status = "review";
    } catch (e) {
      console.error(e);
      job.status = "error";
      job.error = e.message;
    } finally {
      saveCache();
    }
  }

  function createJob(job, body) {
    if (job.status !== "review") throw new ApiError(`Job is ${job.status}, not ready to create.`);
    const c = loadCache();
    for (const pick of body.manual || []) c[cacheKey(job.src, job.dst, pick.source)] = { ...pick.target, score: 1 };
    saveCache();
    const plans = new Map((body.playlists || []).map(p => [p.index, p]));
    job.status = "creating";
    (async () => {
      const dst = provider(job.dst);
      for (const [i, pl] of job.playlists.entries()) {
        const plan = plans.get(i);
        if (!plan) { pl.state = "skipped"; continue; }
        const ids = [...new Set(plan.track_ids || [])];
        const name = (plan.name || pl.new_name).trim() || pl.new_name;
        pl.state = "creating";
        if (plan.target_id) {
          // Update an earlier copy: only add songs that aren't in it yet.
          pl.new_name = job.dst_playlists.find(p => p.id === plan.target_id)?.name || name;
          pl.updated = true;
          try {
            const existing = new Set(await dst.listIds(plan.target_id));
            const fresh = ids.filter(id => !existing.has(id));
            pl.already_there = ids.length - fresh.length;
            await addToPlaylist(job, pl, dst, fresh, plan.target_id, 0);
          } catch (e) { console.error(e); pl.state = "error"; pl.error = e.message; }
        } else {
          pl.new_name = name;
          await addToPlaylist(job, pl, dst, ids, null, 0);
        }
      }
      job.status = "done";
    })();
  }

  // Create (when pid is null) and fill a playlist; on failure, remember the remainder so it can be resumed.
  async function addToPlaylist(job, pl, dst, ids, pid, alreadyAdded) {
    let added = 0;
    pl.created_count = alreadyAdded;
    try {
      if (!pid) pid = await dst.createEmpty(pl.new_name, pl.description);
      pl.playlist_id = pid;
      if (pl.source_id) { // remember the copy so the next run can update it instead of duplicating
        const clones = store.get("clones", {});
        clones[cloneKey(job.src, pl.source_id, job.dst)] = pid;
        store.set("clones", clones);
      }
      pl.url = dst.url(pid);
      await dst.addItems(pid, ids, n => { added += n; pl.created_count = alreadyAdded + added; });
      pl.state = "created";
      return true;
    } catch (e) {
      console.error(e);
      pl.state = "error";
      pl.error = e.message;
      if (pid && added < ids.length) {
        const pending = store.get("pending", []);
        pending.push({ service: job.dst, from: job.src, playlist_id: pid, name: pl.new_name, url: pl.url,
                       ids: ids.slice(added), added: alreadyAdded + added, total: alreadyAdded + ids.length });
        store.set("pending", pending);
        pl.error = `${e.message} Added ${alreadyAdded + added} of ${alreadyAdded + ids.length} tracks; the rest are saved under "Unfinished playlists" so you can resume later.`;
      }
      return false;
    }
  }

  function resumePending(index) {
    const pending = store.get("pending", []);
    const p = pending[index];
    if (!p) throw new ApiError("Nothing to resume.", 404);
    pending.splice(index, 1);
    store.set("pending", pending);
    const pl = { id: p.playlist_id, name: p.name, new_name: p.name, description: "", state: "creating",
                 total: p.total, done: 0, matches: [], playlist_id: p.playlist_id, url: p.url, error: null };
    const job = { id: newId(), src: p.from, dst: p.service, status: "creating", error: null, warning: null, playlists: [pl] };
    jobs.set(job.id, job);
    addToPlaylist(job, pl, provider(p.service), p.ids, p.playlist_id, p.added).then(() => { job.status = "done"; });
    return job;
  }

  /* ---------- status ---------- */
  let spotifyUser = null;
  async function status() {
    const sp = { client_id: store.get("spotify_client_id") || "", signed_in: false, user: null, redirect_uri: redirectUri() };
    if (store.get("sp_token")) {
      try {
        spotifyUser ||= await spApi("me");
        sp.signed_in = true;
        sp.user = spotifyUser.display_name || spotifyUser.id;
      } catch (e) { sp.error = e.message; }
    }
    const t = store.get("yt_token");
    const yt = { client_id: store.get("google_client_id") || "", origin: location.origin,
                 signed_in: !!t && Date.now() < t.expires_at, quota: { used: quota().used, limit: YT_QUOTA } };
    return { mode: "web", spotify: sp, ytmusic: yt, pending: store.get("pending", []) };
  }

  /* ---------- router ---------- */
  async function route(path, body) {
    const url = new URL(path, location.origin);
    const q = k => url.searchParams.get(k) || "";
    const p = url.pathname;
    if (body === undefined) {
      if (p === "/api/status") return status();
      if (p === "/api/playlists") {
        const lists = await provider(q("service")).listPlaylists();
        return [{ id: LIKED, name: "Liked songs", count: null, liked: true }, ...lists];
      }
      if (p === "/api/search") {
        const text = q("q").trim();
        return text ? provider(q("service")).search({ id: "", title: text, artists: [] }) : [];
      }
      const m = /^\/api\/jobs\/([^/]+)$/.exec(p);
      if (m) {
        if (!jobs.has(m[1])) throw new ApiError("Unknown job", 404);
        return jobs.get(m[1]);
      }
    } else {
      if (p === "/api/spotify/client-id" || p === "/api/google/client-id") {
        const id = String(body.client_id || "").trim();
        const google = p.includes("google");
        if (google ? !/^[\w.-]+\.apps\.googleusercontent\.com$/.test(id) : !/^[a-zA-Z0-9]+$/.test(id)) {
          throw new ApiError(google ? "A Google client ID ends in .apps.googleusercontent.com." : "That doesn't look like a Spotify client id.");
        }
        store.set(google ? "google_client_id" : "spotify_client_id", id);
        if (google) { store.del("yt_token"); loadGoogle().catch(() => {}); }
        else { store.del("sp_token"); spotifyUser = null; }
        return status();
      }
      if (p === "/api/logout") {
        if (body.service === "spotify") { store.del("sp_token"); spotifyUser = null; }
        else {
          const t = store.get("yt_token");
          store.del("yt_token");
          if (t && window.google?.accounts?.oauth2) google.accounts.oauth2.revoke(t.access_token, () => {});
        }
        return status();
      }
      if (p === "/api/jobs") return startJob(body);
      const m = /^\/api\/jobs\/([^/]+)\/create$/.exec(p);
      if (m) {
        const job = jobs.get(m[1]);
        if (!job) throw new ApiError("Unknown job", 404);
        createJob(job, body);
        return job;
      }
      if (p === "/api/pending/resume") return resumePending(+body.index);
      const stop = /^\/api\/jobs\/([^/]+)\/stop$/.exec(p);
      if (stop) {
        const job = jobs.get(stop[1]);
        if (!job) throw new ApiError("Unknown job", 404);
        job.stop_requested = true;
        return job;
      }
      if (p === "/api/pending/dismiss") {
        const pending = store.get("pending", []);
        pending.splice(+body.index, 1);
        store.set("pending", pending);
        return status();
      }
    }
    throw new ApiError("Not found", 404);
  }

  window.Backend = {
    mode: "web",
    async api(path, body) { return snapshot(await route(path, body)); },
    connectSpotify() { spotifyLogin().catch(e => window.dispatchEvent(new CustomEvent("backend-error", { detail: e.message }))); },
    connectYouTube,
    async init() {
      if (store.get("google_client_id")) loadGoogle().catch(() => {});
      const params = new URLSearchParams(location.search);
      if (params.has("code") || (params.has("error") && params.has("state"))) {
        let msg;
        try { await spotifyCallback(params); msg = "?connected=spotify"; }
        catch (e) { msg = "?error=" + encodeURIComponent(e.message); }
        history.replaceState(null, "", location.pathname + msg);
      }
    },
    _test: { normalizeTitle, normalizeArtist, score, ratio, queryTitle, ytTrack, isoDuration },
  };
})();
