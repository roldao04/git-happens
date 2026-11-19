// The three ways to run the phone app, one command each: setup (the first time), what the app talks to,
// and the app over HTTPS for the phone (https://<laptop IP>:5174/, nothing to add to the address).
//
//   npm run demo        # the demo house, simulated in the app. No kit, no .env. The mic uses this laptop's voice model.
//   npm run home        # your real home (the kit) and the real Alfred (LLM), look-only: nothing is ever sent
//   npm run home:live   # the same, and changes really happen: the lamp and the plug, never the alarm
//
// Add --voice or --no-voice to change the answer about the voice model. Ctrl-C stops everything.
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { APP_DIR } from "./certs.mjs";
import { REPO, setup } from "./setup.mjs";

const [mode = "demo", ...rest] = process.argv.slice(2);
if (!["demo", "home", "live"].includes(mode)) {
  console.error("usage: node scripts/start.mjs demo|home|live   (npm run demo, npm run home, npm run home:live)");
  process.exit(2);
}

// One mode at a time: they share the ports (the API or voice server on 4790, the app on 5174, the helper on 5175).
const busy = (port) => new Promise((resolve) => {
  const s = net.createServer().once("error", () => resolve(true)).once("listening", () => s.close(() => resolve(false)));
  s.listen(port, "0.0.0.0");
});
for (const port of [4790, 5174, 5175]) {
  if (await busy(port)) {
    console.error(`port ${port} is in use: is npm run demo, home or home:live already running? Stop it first (Ctrl-C there).`);
    process.exit(1);
  }
}

// Your home: the loop reads the kit with the sign-in Claude Code uses (tools/connected_home_auth.py).
if (mode !== "demo" && !fs.existsSync(path.join(os.homedir(), ".config", "connected-home", "tokens.json"))) {
  console.error("Not signed in to the kit yet. Once, from the repo root: python3 tools/connected_home_auth.py login (it opens the browser).");
  process.exit(1);
}

const state = await setup({ argv: rest.filter((a) => a === "--voice" || a === "--no-voice") }); // quick once done
const children = [];
const stop = (code = 0) => {
  for (const c of children) if (c.exitCode === null) c.kill("SIGTERM");
  process.exit(code);
};
process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));
const run = (args, onExit) => {
  const child = spawn("uv", args, { cwd: REPO, stdio: ["ignore", "inherit", "inherit"] });
  child.on("exit", (code) => { if (code) onExit(code); });
  children.push(child);
};

if (mode === "demo") {
  // The demo house lives in the app; only the microphone needs this laptop: a voice-only server, no loop.
  if (state.voiceReady) {
    run(["run", "--extra", "voice", "python", "-m", "source.voice", "--serve", "4790"],
      (code) => console.log(`\ndemo: the voice server stopped (code ${code}): no microphone until you start again.`));
  } else {
    console.log("demo: no voice model (npm run setup -- --voice installs it), so no microphone. Typing works.");
  }
} else {
  const live = mode === "live";
  const args = ["run", ...(state.voiceReady ? ["--extra", "voice"] : []), "python", "-m", "source", ...(live ? [] : ["--look-only"]), "--api"];
  console.log(live
    ? "home:live: Alfred and the phone really switch the lamp and plug (never the alarm)."
    : "home: look-only. Alfred reads your home and answers, but nothing is sent (npm run home:live sends).");
  run(args, (code) => console.log(`\n${mode}: the loop stopped (code ${code}). Is .env set up (copy .env.example, add CGH_API_KEY)? Without the kit: npm run demo.`));
}

const vite = spawn(process.execPath, [path.join(APP_DIR, "node_modules", "vite", "bin", "vite.js"), "--host", "--mode", "phone"],
  { cwd: APP_DIR, stdio: "inherit", env: { ...process.env, VITE_ALFRED_MODE: mode === "demo" ? "demo" : "home" } });
vite.on("exit", (code) => stop(code ?? 0));
children.push(vite);
