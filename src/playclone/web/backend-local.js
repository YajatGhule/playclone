"use strict";
// Backend for the local app: talks to the Python server (playclone ui).
window.Backend = {
  mode: "local",
  async api(path, body) {
    const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({ error: r.statusText }));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  },
  connectSpotify() { location.href = "/api/spotify/login"; },
};
