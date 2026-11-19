# Phone API

What the phone app (`mobile-interaction-interface/`) asks the loop. The API is served from inside the loop: `uv run python -m source --api` (`source/phone_api.py`). The TypeScript shapes are in `mobile-interaction-interface/src/api/types.ts`.

## Running it
- **From the app:** `npm run home` in `mobile-interaction-interface/` starts `python -m source --look-only --api` and the app over HTTPS; `npm run home:live` drops `--look-only`.
- **Look-only** (`--look-only`): the real home is read and the agent is the LLM, but no write is ever sent. A person's writes answer `blocked` with the reason, and so do the agent's (it says so in its reply); Alfred's own decisions and rules don't run their writes.
- **Localhost only.** Port 4790 (`--api-port`, `ALFRED_API_PORT`). Like the simulator's server, it checks Host and Origin. The app's dev server proxies `/api` to it, so the phone talks to a single HTTPS origin.
- **Dry run.** `--stub` and `--replay` send nothing: writes come back `sent` / `unconfirmed`. Tests use this.
- **Without `OPENROUTER_API_KEY`** the chat says it can't think right now; the devices and the plan still work.

## Endpoints
| Call | Body | Answer |
|---|---|---|
| `GET /api/home` | | `{now, household, live, dryRun, lookOnly, devices: [{id, type, name, values, controls, reachable, lastSeen}], people: {home: [{id, name}], guests} \| null, climate: {roomC, targetC, mode, heating} \| null}` |
| `GET /api/status` | | `{title, since, changes: [{deviceId, device, capability, from, to}], why, decisionId, byAlfred, planned: [PlanItem], suggestions: [PlanItem], notSent: [PlanItem], learning: {daysWatched} \| null}` |
| `POST /api/devices/<id>` | `{"body": {"type", "value"}}` | `{status: sent\|blocked\|failed, verified, reason?, note?}` |
| `POST /api/plan/<id>/skip` · `/unskip` | `{}` | `PlanItem` |
| `POST /api/plan/<id>/answer` | `{"yes": bool}` | `PlanItem` |
| `POST /api/undo` | `{"decision": "<id>"}` | `[{status, verified, reason?}]` |
| `POST /api/chat` | `{"text", "lang"}` | `{text, blocks: [], actions: [{status, verified, deviceId, body, reason?}], lang}` |
| `GET /api/voice` · `POST /api/voice` | WAV, 16 kHz mono | `200` / `{text, lang, ms}`; `501` without the voice extra |

- **PlanItem** is `{id, at, mode: do|suggest, status, title, does, reason, confidence, answer: yes|no|skip|undo|null}`.
- **`notSent`** is look-only's "would have": the last 12 h of decisions whose write was refused only because nothing is sent. **`learning`** is set until the first habit is learned.
- **Errors** are `{"error": "<plain words>"}` with `400`, `404`, `409` (that decision can't take that answer now; undo in look-only) or `503` (no poll yet, with the reason when a poll failed, e.g. not signed in to the kit).

## What it does in the loop
- **Writes go through `guard.guarded_write` as a person.** A plug may be switched on. The thermostat keeps 16–23 °C, ≤ 3 °C per change and ≤ 18 °C while no known phone is home, without Alfred's hold or rate limit. The alarm can't be written.
- **The changes a write causes are labelled `source = "app"`.** The learners count them as the person's.
- **The status is derived, not stored.** The title comes from an occasion, else from who is home, else from the habit behind Alfred's last change. The changes are the sent writes of decisions that ran since, with their values before. `source/status.py`.
- **People can answer Alfred's decisions.** They skip, answer yes or no, or undo, which is stored in the decision's `meta.person`. A "yes" keeps a decision `do` through re-planning and may still run up to 2 h late.
- **Overrides are recorded.** Undo, skip, decline, and changing back by hand what Alfred just set all go in the `overrides` table. The agent sees them as `baselines.overrides_7d`.
- **The live loop learns every night.** On the first poll of each day it runs the learners on everything up to the end of the day before (`learned_days` keeps it to once a day), as `source/pipeline.py` does for a simulated home. So habits, the plan and suggestions appear after a few days of watching. Replays don't.
- **Chat runs the agent once**, as `source/ask.py` does: a `person` reason, `allow_plug_on`, and `(in pt)` when the app knows the language.
- **Voice is Parakeet TDT 0.6B v3** (int8 ONNX, CPU) through `onnx-asr`, as the `voice` extra. `npm run setup` asks before installing it.
  - On an Intel Core Ultra 7 155H, 4 s of audio takes about 0.3–0.75 s once the model is loaded; the first request after starting takes about 2.5 s.
  - It recognises the language itself and has no setting for it. `lang` is `null`, and the app works out Auto from the text.
  - Test it with `uv run --extra voice python -m source.voice speech.wav`.
  - `python -m source.voice --serve 4790` serves only `/api/voice`, with no loop. That's what `npm run demo` starts, so the demo house hears the real microphone.
