# Alfred · team GitHappens

<img src="assets/alfred-lockup-on-light.svg" alt="Alfred" width="220">

**A home that learns its own rhythms and runs the house ahead of them.**
Alfred watches the devices of an Altice Connected Home through its MCP. From the home's own history it learns when people wake up, come home and have friends over. Then it gets the house ready before they need it, with no rules to write.
Aveiro Tech City Hackathon 2026, Altice Labs Desafio #3 ("Connected Home, Reimagined").

## What it does for people
- **Warm when you get home.** It learns each person's arrival (from their phone on the Wi-Fi) and how fast the house heats, then starts the heating ahead. In the demo house the room is 21.2 °C when Ana walks in; it was 18.3 °C before Alfred.
- **Mornings that match the day.** Separate weekday and weekend windows, learned, not set.
- **Knows a party isn't a routine.** It recognises a gathering from past ones, lowers the heating and moves tomorrow's warm-up later.
- **Stays in your hands.**
  - It acts only when the evidence is strong (confidence ≥ 0.75, computed from counts) and only suggests otherwise.
  - The phone app shows what it did and why. You can skip, undo or answer, or talk to it in Portuguese or English.
  - A person's change always wins.

How it works: [`ARCHITECTURE.md`](ARCHITECTURE.md). The maths: [`TECHNICAL.md`](TECHNICAL.md).

## Set up
Needs [uv](https://docs.astral.sh/uv/) (Python 3.12) and Node 20+.
```
cp .env.example .env    # OPENROUTER_API_KEY for the LLM (optional: without it, replies are scripted);
                        # CGH_API_KEY for the real kit
```
For the real kit only:
```
cp .mcp.example.json .mcp.json            # add the team API key and the absolute path to tools/connected_home_auth.py
python3 tools/connected_home_auth.py login   # once: browser sign-in to the Altice MCP
```
Optional: `uv sync --extra voice` for speech to text on this computer (about 650 MB).

## Run
**1. The demo house (no kit).** 60 simulated days are learned, then four moments are lived with Alfred awake.
```
uv run python -m source.demo --household demo                    # once, about 15 min; writes data/demo/
ALFRED_LLM_CACHE=only uv run python -m source.simulate --serve   # http://localhost:4781/visualizer?household=demo
```
The moments bar jumps to *Ana arrives* (day 60, 18:00), *the gathering* (day 54, 20:30), *the morning after* (day 55, 07:45) and *a weekday morning* (day 59, 06:30). "Alfred thinks" shows each wake, the LLM's sentence, the confidence and what ran. The knowledge tab shows the habits, the warm-up, and planted vs found.

**2. The phone app.** Details in [`mobile-interaction-interface/README.md`](mobile-interaction-interface/README.md).
```
cd mobile-interaction-interface && npm install
npm run demo        # the demo house, no kit
npm run home        # the real kit with the real Alfred, look-only: nothing is sent
npm run home:live   # the same, and changes really happen
```

**3. Alfred on the real home, without the app.**
```
uv run python -m source --look-only --api   # reads the kit, asks the LLM, sends nothing
uv run python -m source --api               # live: writes go through the guard
uv run python -m source --replay tests/fixtures/morning.jsonl   # a recorded morning, no kit
uv run --extra voice python -m source --api --hear-camera       # also listens to the camera's microphone
```
The live loop learns every night, so habits, the plan and suggestions appear after a few days of watching.

With `--hear-camera` (needs `CAMERA_RTSP_URL` in `.env` and `ffmpeg`), what is said with "Alfred" in it goes to Alfred like a question from the app. Anything said without "Alfred" is never logged or kept.

**4. The jury dashboard** plays back a recorded run: `cd dashboard && npm install && npm run dev` ([`dashboard/README.md`](dashboard/README.md)).

## Test
```
uv run python -m unittest discover -s tests -t .           # Python, about 3 min
cd dashboard && npm test && npm run typecheck
cd mobile-interaction-interface && npm test && npm run typecheck
```

## Documents
**For evaluators** (one page each, self-contained). The PDFs in [`deliverables/`](deliverables/) are what we hand in; the markdown files are their editable source.

| Document | PDF | What it covers |
|---|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | [PDF](deliverables/Alfred-Architecture.pdf) | What people get, how Alfred fits together, the devices |
| [`TECHNICAL.md`](TECHNICAL.md) | [PDF](deliverables/Alfred-Technical.pdf) | How habits are learned, how departures are detected, where the LLM comes in, safety, results |
| [`MARKET.md`](MARKET.md) | [PDF](deliverables/Alfred-Market.pdf) | How Alfred compares with the market, where it wins and where it is behind |
| [`BUSINESS.md`](BUSINESS.md) | [PDF](deliverables/Alfred-Business.pdf) | Go-to-market, the free trial and subscription, what the LLM costs |
| Executive summary | [PDF](<deliverables/Sumário Executivo GitHappens.pdf>) | The 2-page summary for the jury (Portuguese) |

**For developers:**

| Document | What it covers |
|---|---|
| [`AGENT.md`](AGENT.md) | Instructions for AI coding agents in this repo |
| [`docs/contracts.md`](docs/contracts.md) | Code map (parts 1–8), contracts C1–C12, data shapes, the store |
| [`docs/`](docs/) | Detailed references: [event loop](docs/event-loop.md), [learners](docs/learners.md), [habits](docs/habits.md), [occasions](docs/occasions.md), [anticipation](docs/anticipation.md), [simulator](docs/simulator.md), [phone API](docs/phone-api.md), [MCP capabilities](docs/mcp-capabilities.md), [kit hardware and safety rules](docs/reference/hardware-list.md) |
| [`deliverables/`](deliverables/) | Hackathon hand-ins and organiser templates |

**Kit safety:**
- Only a small lamp on the plug.
- Never arm the alarm.
- Never re-pair or reset devices.
- Warn people before testing the smoke alarm.

The full rules are in [`docs/reference/hardware-list.md`](docs/reference/hardware-list.md).
