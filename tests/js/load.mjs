// Load site/backend.js (a classic browser script) into Node and return its test hooks.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const src = readFileSync(fileURLToPath(new URL("../../site/backend.js", import.meta.url)), "utf8");
const ctx = { console, URL, URLSearchParams, TextEncoder, crypto: globalThis.crypto, Intl, setTimeout };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
export const M = ctx.Backend._test;
