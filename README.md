# Alfred · team GitHappens

<img src="assets/githappens-logo.jpeg" alt="GitHappens logo" width="120">

**A home that builds its own sensors:** Alfred learns what the devices' signals mean from the home's own history, and shows that as new sensors you can see, ask about and talk to.
Aveiro Tech City Hackathon 2026, Altice Labs Desafio #3 ("Connected Home, Reimagined").

## Docs
| Doc | What it's for | Read when |
|---|---|---|
| [`docs/learned-sensors-plan.md`](docs/learned-sensors-plan.md) | **The build plan:** learned sensors (defined + LLM-inferred), simulator, demo | First |
| [`docs/build-tree.md`](docs/build-tree.md) | **The build tree:** every task, owners, schedule, API contract, the Alfred Hub (chat + voice), visual identity, demo | Before picking up a task |
| [`docs/synapse.md`](docs/synapse.md) | The story and reasoning (semantic layer, verification, memories); to be rewritten around Alfred | For the why |
| [`docs/build-path.md`](docs/build-path.md) · [`docs/use-cases.md`](docs/use-cases.md) | The earlier product path (①–⑪) and use cases; partly superseded by the build tree | For context |
| [`docs/event-loop.md`](docs/event-loop.md) | **The event loop:** polling, triggers, heartbeat, when the LLM wakes, how verification and self-authored rules plug in | Before writing the poller or agent |
| [`docs/mcp-capabilities.md`](docs/mcp-capabilities.md) | What the Altice MCP can see, change and can't do; device IDs | Before writing code |
| [`docs/altice-hackathon-brief.md`](docs/altice-hackathon-brief.md) | Challenge, schedule, judging criteria, rules | Before the pitch or report |
| [`deliverables/README.md`](deliverables/README.md) | What we hand in, limits and deadlines | Before each deadline |
| [`deliverables/templates/`](deliverables/templates/) | Organiser templates: slide deck (`.pptx`), executive summary links | When starting a deliverable |
| [`docs/reference/hardware-list.md`](docs/reference/hardware-list.md) | Kit hardware: models, radios, how each device reaches the MCP, **safety rules** | Before touching the physical kit |
| [`docs/reference/`](docs/reference/) | Organiser PDFs and strategy research | When you need the source |

## Project drive
[Shared team folder](https://drive.google.com/drive/folders/1SPYUYUx7WPJ9xSI5GlHnbtpvS0-Cky2o?usp=sharing) on Google Drive, with templates and working files for the deliverables.

## Key dates
- **Wed 18 Nov, afternoon:** mentor checkpoint (online, mandatory)
- **Wed 18 Nov, 19:00:** approach report due (max 2 pages)
- **Fri 20 Nov, 17:30:** executive summary (PDF, max 2 pages) + slides + repo link
- **Fri 20 Nov, 18:00:** pitches (3 min + 1 min Q&A; finalists 5 min + 3 min Q&A)

## Setup: connect to the Altice MCP
1. `cp .mcp.example.json .mcp.json`, then put in the team API key and the absolute path to `tools/connected_home_auth.py`.
2. `python3 tools/connected_home_auth.py login` (one-time browser sign-in with the team account).
3. Start Claude Code in this folder and check `/mcp` shows `connected-home` connected.

## Run the event loop
```
cp .env.example .env     # add the team API key as CGH_API_KEY, and OPENROUTER_API_KEY for the LLM agent
uv run python -m source --replay tests/fixtures/morning.jsonl   # demo morning, no kit needed
uv run python -m source                                         # live: LangGraph agent (can change the lamp/plug)
uv run python -m source --stub                                  # live, stub agent: no LLM, no writes
uv run python -m source.ask "make the lamp blue"               # ask the agent once, on the live house
uv run python -m source.show events                             # what it saw
tail -f data/logs/loop.log                                      # logs
```
Without `OPENROUTER_API_KEY` (or with `--stub`) the stub agent runs. Replays give the LLM no tools. Details: [`docs/event-loop.md`](docs/event-loop.md).

## Repo structure
```
README.md               you are here
.mcp.example.json       MCP config template (the real .mcp.json is gitignored)
docs/
  learned-sensors-plan.md  the build plan
  build-tree.md         tasks, owners, schedule, hub, visual identity
  synapse.md            the story and reasoning
  build-path.md, use-cases.md  earlier path and use cases
  event-loop.md         polling loop + agent triggers design
  mcp-capabilities.md   MCP reference
  altice-hackathon-brief.md
  reference/            organiser material + research (read-only)
deliverables/           report, executive summary, slides (+ organiser templates)
assets/                 logo and images
tools/                  connected_home_auth.py (OAuth helper used by .mcp.json)
source/                 event loop: poll the MCP, diff, store, triggers, agent (LangGraph + stub), logs, show
tests/                  replay tests; fixtures = a real get_home snapshot + a demo morning
```
Planned ([`docs/build-tree.md`](docs/build-tree.md)): more of `source/` (summary tables, sensors, learning loop, Alfred server, chat, voice) · the simulator · `hub/` (the Alfred Hub: kiosk, chat, voice) · `assets/alfred/` (logo and avatar).

## Conventions
- **Never commit secrets:** `.mcp.json`, `.env` and tokens stay local (see `.gitignore`).
- Docs in English, file names in kebab-case.
- Scenarios we create on the Altice platform are named `SY · <name> · v<n>`. They can't be deleted, so create them only after the format is confirmed.
- `docs/reference/` holds source material (organiser docs, kit notes): summarise it in `docs/` rather than copying it.
- Physical kit: only a small lamp on the smart plug, warn people before testing the smoke alarm, never arm the alarm (see the hardware list).
