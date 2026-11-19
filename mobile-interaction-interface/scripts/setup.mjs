// Everything the phone app needs on this laptop, in one go:
//   1. HTTPS the phone trusts (npm run certs);
//   2. the voice model, only if you say yes: Parakeet TDT 0.6B v3, about 650 MB, runs on this computer.
//      Say no and voice simply isn't there. The answer is remembered in .setup.json.
//
//   npm run setup                 # asks about voice the first time
//   npm run setup -- --voice      # install it (or retry)
//   npm run setup -- --no-voice   # don't
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline/promises";
import { pathToFileURL } from "node:url";
import { APP_DIR, ensureCerts } from "./certs.mjs";

export const REPO = path.resolve(APP_DIR, "..");
const STATE = path.join(APP_DIR, ".setup.json");

export function readState() {
  try {
    return JSON.parse(fs.readFileSync(STATE, "utf8"));
  } catch {
    return {};
  }
}

function saveState(s) {
  fs.writeFileSync(STATE, JSON.stringify(s, null, 2) + "\n");
}

function run(cmd, args) {
  console.log(`setup: ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { cwd: REPO, stdio: "inherit" });
  if (r.error) return r.error.code === "ENOENT" ? `${cmd} isn't installed` : r.error.message;
  return r.status === 0 ? null : `${cmd} ${args[0]} stopped with code ${r.status}`;
}

async function ask(question) {
  if (!process.stdin.isTTY) return null;
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    return /^y(es)?$|^s(im)?$/i.test((await rl.question(question)).trim());
  } finally {
    rl.close();
  }
}

/** Certificates always; the voice model if wanted. Returns the remembered state ({voice, voiceReady}). */
export async function setup({ argv = process.argv.slice(2) } = {}) {
  await ensureCerts();
  const state = readState();
  const flag = argv.includes("--voice") ? true : argv.includes("--no-voice") ? false : undefined;
  let voice = flag ?? state.voice;
  if (voice === undefined) {
    voice = await ask("\nInstall the voice model? Parakeet TDT 0.6B v3: about 650 MB, runs on this computer, Portuguese and English. [y/N] ");
    if (voice === null) {
      console.log("setup: no terminal to ask in, so no voice (npm run setup -- --voice to install it)");
      voice = false;
    }
  }
  state.voice = voice;
  if (!voice) {
    state.voiceReady = false;
    console.log("setup: voice is off. The app works by typing; npm run setup -- --voice adds it later.");
  } else if (state.voiceReady && flag === undefined) {
    console.log("setup: voice model already installed");
  } else {
    const failed = run("uv", ["sync", "--extra", "voice"]) ?? run("uv", ["run", "--extra", "voice", "python", "-m", "source.voice", "--download"]);
    state.voiceReady = !failed;
    console.log(failed ? `setup: voice not installed (${failed}). The app works by typing; try again with npm run setup -- --voice.`
      : "setup: voice model ready");
  }
  saveState(state);
  return state;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await setup();
}
