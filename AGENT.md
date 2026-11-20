# Instructions for AI coding agents

How to work in this repo. What Alfred is: [`README.md`](README.md). Code map, contracts and data shapes: [`docs/contracts.md`](docs/contracts.md). The concepts, written for evaluators: [`ARCHITECTURE.md`](ARCHITECTURE.md), [`TECHNICAL.md`](TECHNICAL.md).

## Layout
```
source/                 Python 3.12 package: the event loop, agent, guard, simulator, demo
  __main__.py           python -m source: the loop (--replay, --stub, --look-only, --api)
  loop.py sources.py normalize.py diff.py store.py clock.py    poll → events → SQLite
  triggers.py actions.py guard.py                               wake rules, hard rules, the write guard
  wake.py context.py agent.py llm_cache.py prompts/            learned wakes, the LLM's input, the agent, its cache
  anticipate.py status.py phone_api.py voice.py                decisions; the phone app's API; speech to text
  simulate.py simulate_web.py sim_data/ demo.py pipeline.py ingest.py recording.py
  learn/                the learners (no LLM except namer.py): rest, happenings, kinds, habits, grouping,
                        recurrence, occasions, forecast, warmup, today, storage, run, namer
tests/                  unittest; fixtures/ holds real get_home snapshots and hand-made histories
dashboard/              React + Vite + TS: the jury dashboard (plays dashboard/public/recording.json)
mobile-interaction-interface/   React + Vite + TS: the phone app (talks only through src/api/client.ts HomeApi)
console/                the 3D visualizer and simulator pages served by source/simulate_web.py; live device console
tools/                  connected_home_auth.py (MCP OAuth helper), extract_smart_home_a.py
docs/                   detailed references (see the table below); docs/reference/ holds organiser material
data/                   runtime output, gitignored: home.sqlite3, logs/, demo/, llm-cache/
```

## Build, run, test
```
uv run python -m unittest discover -s tests -t . -v        # full suite, ~3 min (simulates months); must pass
uv run python -m unittest tests.test_wake -v               # one module
uv run python -m source --replay tests/fixtures/morning.jsonl   # the loop with no kit and no writes
uv run python -m source.pipeline --household rosa --days 14 --out data/sim/rosa/ --replace   # simulate + learn
uv run python -m source.demo --household demo              # pre-compute the demo (~15 min)
cd dashboard && npm test && npm run typecheck && npm run build
cd mobile-interaction-interface && npm test && npm run typecheck && npm run build
```
Settings come from `.env` (see `.env.example`). Without `OPENROUTER_API_KEY` the agent is the stub and the demo's replies are scripted.

## Rules the code depends on
- **No real clock.** Time comes from the data. The store keeps epoch ms; the learners use naive Lisbon datetimes through `source/clock.py`. Tests fail if ingest reads `time.time()`.
- **Find devices by type, never by id.** Ids change when a device is re-paired. Read a signal's device type with `source.learn.device_type(signal)`, never `signal.split(".")[0]` (signals can be `plug:kettle.instantPower`).
- **Every device write goes through `guard.guarded_write`**, via `actions.run_actions` or the agent's tool wrapper. Don't add a write path around it. Replays use `DryRunActuator`, look-only uses `LookOnlyActuator`.
- **The LLM never sets a confidence.** Code computes it (`anticipate.decide`: base × (1 − doubt)). The `do` threshold is `DO_AT = 0.75` in `source/anticipate.py`.
- **No new thresholds in the learners.** The only knobs are `ALPHA`, `MIN_OCCURRENCES` and `MAX_LAG_MIN` in `source/learn/__init__.py`. Any other cut-off must come from a chance test on the home's own data.
- **Read other parts defensively.** A missing table or module leaves its piece empty; it never raises into the loop. Code on the loop's path must not throw, because the smoke trigger runs after it.
- **Alfred's own writes are not habits.** Events caused by Alfred are stored with `source = "agent"` (or `scenario:<rule>`); the happenings leave them out. Keep it that way when adding writes.
- **Keep the phone app's mock guard in step with the real one.** `mobile-interaction-interface/src/api/mock/guard.ts` mirrors `source/guard.py`; change both together.
- **Contracts C1–C12** (shapes shared between parts) are listed in [`docs/contracts.md`](docs/contracts.md#contracts). Code comments cite them by number, and the pipeline's parts 1–8 by its code map.

## Tests
- Python uses `unittest` (no pytest). Put new fixtures in `tests/fixtures/`.
- **Never call a real LLM or the real kit in tests.**
  - Use a fake model; the reply cache only stores models with a `model_name`.
  - Use `ReplaySource`, or `--stub`, for the kit.
- The simulator is deterministic by seed. Assert on what the learners find, not on random draws.
- Frontends use Vitest and Testing Library. Run typecheck too: the build runs `tsc --noEmit`.

## The physical kit and the MCP
- **Never:**
  - arm the alarm (`patch_home_security_status`);
  - call the hub's `restart` or `factoryReset`;
  - re-pair or reset devices.
- **Don't create scenarios or timers on the platform.** They can't be deleted through the MCP; the agent's tool allowlist excludes them.
- **Plug on needs a person.** Only a small lamp is on the plug.
- **Live writes only with `npm run home:live` or `python -m source --api`.** Prefer `--look-only` or `--replay` while developing.
- **Python clients reach the MCP over HTTP with `tools/connected_home_auth.py` tokens** (`source/sources.py`); a plain client gets a Cloudflare 403.
- **Never commit secrets:** `.env`, `.mcp.json`, tokens and `data/` are gitignored.

## Conventions
- **Comments:** short and plain, saying what and why. Point to docs by path (`TECHNICAL.md`, `docs/habits.md`).
- **Dependencies:** none new without a clear need (`pyproject.toml`, the apps' `package.json`).
- **Docs:** English, concise, kebab-case file names under `docs/`. When behaviour changes, update the doc that describes it (table below). Don't add new top-level documents.
- **Design system:** the apps' `public/ds/` is a vendored copy (see `public/ds/SOURCE.md`). Don't edit it by hand.
- **Commits:** conventional style, `feat(learn): …`, `fix(wake): …`, `docs(loop): …`. One topic per branch, merged to `master` by PR.

## Which document for which change
| Changing | Read first, and keep up to date |
|---|---|
| Polling, triggers, hard rules, the agent, the cache | [`docs/event-loop.md`](docs/event-loop.md) |
| Rest, happenings, kinds, links, patterns | [`docs/learners.md`](docs/learners.md) |
| Habits, rhythms, weekday windows, warm-up | [`docs/habits.md`](docs/habits.md) |
| Occasions | [`docs/occasions.md`](docs/occasions.md) |
| Forecast, decisions, learned wakes, proposals, thermostat limits | [`docs/anticipation.md`](docs/anticipation.md) |
| The guard or safety | [`TECHNICAL.md`](TECHNICAL.md#4-safety), [`docs/reference/hardware-list.md`](docs/reference/hardware-list.md) |
| The simulator or the demo house | [`docs/simulator.md`](docs/simulator.md) |
| The phone app or its API | [`docs/phone-api.md`](docs/phone-api.md), [`mobile-interaction-interface/README.md`](mobile-interaction-interface/README.md) |
| The jury dashboard | [`dashboard/README.md`](dashboard/README.md) |
| What the MCP can read or write | [`docs/mcp-capabilities.md`](docs/mcp-capabilities.md) |
| Components, contracts, data shapes, the store | [`docs/contracts.md`](docs/contracts.md) |
| The evaluator documents (architecture, technical, market, business) | Keep them self-contained: no file paths, code names or repo links |
