import assert from "node:assert/strict";
import { test } from "node:test";
import { M } from "./load.mjs";

const T = (title, artists, d = null, isrc = null, id = "x") => ({ id, title, artists, duration_s: d, isrc });

test("normalizeTitle", () => {
  const cases = {
    "Bohemian Rhapsody - Remastered 2011": "bohemian rhapsody",
    "Blinding Lights (Official Video)": "blinding lights",
    "Stay (feat. Justin Bieber)": "stay",
    "Peaches ft. Daniel Caesar": "peaches",
    "Despacito [Official Music Video]": "despacito",
    "Café del Mar": "cafe del mar",
    "Hey Jude (Live)": "hey jude live",
    "Song (Remix)": "song remix",
    "तुम ही हो": "तुम ही हो", // non-Latin titles survive (JS \w is ASCII-only)
  };
  for (const [raw, want] of Object.entries(cases)) assert.equal(M.normalizeTitle(raw), want, raw);
});

test("normalizeArtist", () => {
  assert.equal(M.normalizeArtist("The Weeknd"), "weeknd");
  assert.equal(M.normalizeArtist("Daft Punk - Topic"), "daft punk");
  assert.equal(M.normalizeArtist("Beyoncé"), "beyonce");
});

test("score", () => {
  assert.equal(M.score(T("a", ["b"], null, "USUM71703861"), T("zzz", ["q"], null, "usum71703861")), 1);
  assert.ok(M.score(T("Bohemian Rhapsody - Remastered 2011", ["Queen"], 354), T("Bohemian Rhapsody", ["Queen"], 355)) >= 0.95);
  assert.ok(M.score(T("Hey Jude", ["The Beatles"], 431), T("Hey Jude (Live)", ["The Beatles"], 431)) < 0.75);
  assert.ok(M.score(T("Hello", ["Adele"], 295), T("Hello", ["Lionel Richie"], 246)) < 0.75);
});

test("ratio matches difflib", () => {
  // values from Python: difflib.SequenceMatcher(None, a, b).ratio()
  assert.equal(M.ratio("abcd", "bcde").toFixed(4), "0.7500");
  assert.equal(M.ratio("blinding lights", "blinding light").toFixed(4), "0.9655");
  assert.equal(M.ratio("", "x"), 0);
});

test("queryTitle strips noise but keeps case", () => {
  assert.equal(M.queryTitle("Blinding Lights (Official Video)"), "Blinding Lights");
  assert.equal(M.queryTitle("Bohemian Rhapsody - Remastered 2011"), "Bohemian Rhapsody");
  assert.equal(M.queryTitle("Levitating (feat. DaBaby)"), "Levitating");
  assert.equal(M.queryTitle("Hey Jude (Live)"), "Hey Jude (Live)");
});

test("ytTrack splits artist and title", () => {
  const v = (title, channelTitle, duration = "PT3M20S") => ({ id: "v", snippet: { title, channelTitle }, contentDetails: { duration } });
  assert.deepEqual(
    [M.ytTrack(v("The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO")).artists[0], M.ytTrack(v("x - y", "z")).title],
    ["The Weeknd", "y"]);
  const topic = M.ytTrack(v("Bohemian Rhapsody - Remastered 2011", "Queen - Topic", "PT5M54S"));
  assert.equal(topic.title, "Bohemian Rhapsody - Remastered 2011"); // Topic channels: title is the real title
  assert.deepEqual([...topic.artists], ["Queen"]);
  assert.equal(topic.duration_s, 354);
  assert.equal(M.isoDuration("PT1H2M3S"), 3723);
});
