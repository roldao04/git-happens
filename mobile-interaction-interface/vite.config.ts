/// <reference types="vitest/config" />
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// The Python side listens on localhost only: the loop's API (npm run home) or the voice-only server
// (npm run demo). The dev server passes /api to it, so a phone on the same Wi-Fi talks to one origin.
// `--mode phone` (scripts/start.mjs) serves HTTPS with this laptop's own certificate (npm run certs):
// phones only allow the microphone on secure pages. VITE_ALFRED_MODE (demo | home) tells the app which.
const API = process.env.ALFRED_API_URL ?? "http://127.0.0.1:4790";
const PORT = 5174;
const CA_PORT = 5175;
const HOME = process.env.VITE_ALFRED_MODE === "home";
const CERTS = path.resolve(__dirname, ".certs");

function lan(): string[] {
  return Object.values(os.networkInterfaces()).flat()
    .filter((a): a is os.NetworkInterfaceInfo => !!a && a.family === "IPv4" && !a.internal)
    .map((a) => a.address);
}

function certificate() {
  const read = (f: string) => {
    try {
      return fs.readFileSync(path.join(CERTS, f));
    } catch {
      throw new Error(`no ${f} in .certs/: run npm run certs (npm run demo and npm run home do it for you)`);
    }
  };
  return { key: read("dev.key"), cert: read("dev.crt") };
}

const guide = (ip: string) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alfred: trust this laptop</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:0 auto;padding:24px;color:#1d1c1a;background:#f8f7f4}
a.b{display:inline-block;padding:12px 18px;margin:8px 0;border-radius:10px;background:#1d1c1a;color:#fff;text-decoration:none}h2{font-size:17px;margin:24px 0 4px}ol{padding-left:20px}</style></head><body>
<h1>Trust this laptop, once</h1><p>So the phone can open Alfred over HTTPS with the microphone, and without a warning.</p>
<a class="b" href="/alfred-dev-ca.crt">Download the certificate</a>
<h2>Android</h2><ol><li>Download it with the button above.</li><li>Settings → Security (or Security &amp; privacy) → More security settings → Encryption &amp; credentials → Install a certificate → <b>CA certificate</b> → Install anyway → pick <i>alfred-dev-ca.crt</i>.</li></ol>
<h2>iPhone</h2><ol><li>Open this page in Safari and download it: “Profile downloaded”.</li><li>Settings → General → VPN &amp; Device Management → <i>Alfred dev</i> → Install.</li><li>Settings → General → About → Certificate Trust Settings → turn on <i>Alfred dev</i>.</li></ol>
<h2>Then</h2><p><a class="b" href="https://${ip}:${PORT}/">Open Alfred</a></p>
<p style="color:#5f5d58;font-size:14px">The certificate only vouches for this laptop's own addresses. Remove it from the phone when you no longer need it.</p></body></html>`;

/** Plain HTTP on port 5175 for the phone's first visit: this guide and the authority's certificate, never its key. */
function caHelper(): Plugin {
  return {
    name: "alfred-ca-helper",
    configureServer(server) {
      const ip = lan()[0] ?? "localhost";
      const helper = http.createServer((req, res) => {
        if (req.url === "/alfred-dev-ca.crt") {
          res.writeHead(200, { "Content-Type": "application/x-x509-ca-cert", "Content-Disposition": 'attachment; filename="alfred-dev-ca.crt"' });
          res.end(fs.readFileSync(path.join(CERTS, "alfred-dev-ca.crt")));
        } else if (req.url === "/" || req.url === "/index.html") {
          res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
          res.end(guide(ip));
        } else {
          res.writeHead(404, { "Content-Type": "text/plain" });
          res.end("not found");
        }
      });
      helper.on("error", (e) => server.config.logger.warn(`certificate helper: ${e.message}`));
      helper.listen(CA_PORT, "0.0.0.0");
      server.httpServer?.on("close", () => helper.close());
      server.httpServer?.once("listening", () => setTimeout(() => {
        server.config.logger.info([
          "",
          "  On the phone (same Wi-Fi):",
          `  1. Once: open http://${ip}:${CA_PORT} and install the certificate it gives you.`,
          `  2. Then: https://${ip}:${PORT}/   (${HOME ? "your home" : "the demo house"})`,
          "",
        ].join("\n"));
      }, 50));
    },
  };
}

/** When the loop isn't running, /api answers one plain sentence (the app shows it) instead of the proxy
 *  printing a connection error for every poll. */
function apiDown(): Plugin {
  const target = new URL(API);
  let known = { at: 0, up: false };
  let logged = 0;
  const reachable = () => new Promise<boolean>((resolve) => {
    const s = net.connect({ host: target.hostname, port: Number(target.port) || 80 });
    const done = (up: boolean) => { s.destroy(); resolve(up); };
    s.setTimeout(400);
    s.once("connect", () => done(true));
    s.once("error", () => done(false));
    s.once("timeout", () => done(false));
  });
  return {
    name: "alfred-api-down",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!req.url?.startsWith("/api/")) return next();
        const now = Date.now();
        if (now - known.at > 2000) known = { at: now, up: await reachable() };
        if (known.up) return next();
        if (now - logged > 60_000) {
          logged = now;
          server.config.logger.warn(HOME
            ? `  Alfred's loop isn't running on ${target.host} yet: the app says "not running" until it starts (npm run home).`
            : `  No voice server on ${target.host} (yet): until it starts, the demo house has no microphone. Without the model: npm run setup -- --voice.`);
        }
        res.statusCode = 503;
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ error: HOME ? "Alfred’s loop isn’t running: start npm run home (or npm run home:live)" : "no voice server: the demo house has no microphone" }));
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  base: "./",
  plugins: mode === "phone" ? [react(), apiDown(), caHelper()] : [react(), apiDown()],
  server: {
    port: PORT,
    https: mode === "phone" ? certificate() : undefined,
    // The API refuses other origins (like source/simulate_web.py); the proxy speaks for the app as localhost.
    proxy: { "/api": { target: API, changeOrigin: true, headers: { origin: API } } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "src/test/setup.ts",
    coverage: {
      provider: "v8",
      include: ["src/**"],
      exclude: ["src/main.tsx", "src/test/**", "src/**/*.test.{ts,tsx}", "src/**/*.d.ts"],
    },
  },
}));
