# Alfred: build path

> **Updated 17 Nov, afternoon:** the team now follows [`learned-sensors-plan.md`](learned-sensors-plan.md), split into tasks in [`build-tree.md`](build-tree.md). ④ (as learned sensors) and ⑨–⑪ continue there; ⑤ Propose, ⑧ Remember and the rule side of ⑥ are now stretch; ⑥–⑦'s guard and read-back stay as built. This file keeps the reasoning behind each step.

What we build, in order: **the product first**, use cases and clients on top. ⛔ = blocking · ⚪ = side track. Concept: [`synapse.md`](synapse.md) · Use cases: [`use-cases.md`](use-cases.md) · Event loop: [`event-loop.md`](event-loop.md) · MCP limits: [`mcp-capabilities.md`](mcp-capabilities.md) · Code: [`githappens/`](../README.md) ([GitHub](https://github.com/roldao04/git-happens))

## Where we are (Tue 17 Nov)
✅ done · 🟡 partly · 🟠 started early · ⬜ not started. PRs [#1–#8](https://github.com/roldao04/git-happens/pulls?q=is%3Apr+is%3Amerged) are merged.

| Step | Status | What exists | Missing |
|---|---|---|---|
| ① Foundations | 🟡 | Formats for state, event, trigger and agent ([`event-loop.md`](event-loop.md) §5) | Meaning objects: moment, routine, rule, memory, virtual sensor, plan |
| ② Connect | 🟡 | Live MCP + replay behind one `fetch()` ([`sources.py`](../source/sources.py)); the console ([`console/`](../console/)); kit partly back | Simulator weeks; a simulator write path (fake MCP) |
| ③ Perceive + wake | ✅ mostly | Poll → diff → SQLite + recording; heartbeat, safety, door, plug and camera triggers; rules that act without the LLM | "Who did it"; triggers the agent can edit |
| ④ Understand → virtual sensors | ⬜ | — | Everything |
| ⑤ Propose | ⬜ | — | Everything |
| ⑥ Act safely | 🟠 | The agent and trigger rules write to the lamp and plug | Approval tiers (the agent can switch the plug **on**); scenario registry |
| ⑦ Verify | 🟠 | The agent's prompt reads the device back | Action ledger + expectation checker; second-device check |
| ⑧ Remember | ⬜ | `agent_runs` and `rule_runs` as raw material | Episodes, recall |
| ⑨ Talk | 🟡 | `source.ask` answers one request | tell · teach · why · invent · plan |
| ⑩ Expose | ⬜ | — | Alfred's MCP server (V needs a small one early) |
| ⑪ Showcase | ⬜ | Console; deliverable templates | Everything |
| 📅 Calendar | ⬜ idea | — | Connector, plans, setup cards |
| ⚪ Side tracks | 🟡 | Use cases ([`use-cases.md`](use-cases.md)) | **Approach report, Wed 18 Nov 19:00**; mentor questions |

## Next up
1. **Record the live kit now:** leave `uv run python -m source --stub` running (it never writes), so Friday's sensors have real history. Re-check every device ID: the contact sensor already has a new one.
2. **Approach report** (Wed 19:00), led by V (*Sensors that don't exist*), plus the mentor questions (scenario format, delete, rate limits).
3. **21 days of simulated data:** frames on change + every 15 min (~20 MB), replayed with `--stub`.
4. **Virtual sensors:** the spec, one evaluator for history and live, and the 5 demo sensors.
5. **Sentence → spec** (with "I'd need X"), a small **Alfred MCP server**, and virtual cards in the console.
6. **Guardrail on writes** (plug on needs a human), then A on virtual sensors, then E.

## Product architecture
```
          Any client, defined later (chat, voice, app, other agents)
                                  │
                Alfred tools (its own MCP server / API)                 [todo ⑩]
                                  │
                 LANGUAGE: ask · tell · teach · "why?"                  [partly ⑨]
                                  │
   ┌──────────────┬───────────────┼───────────────┬──────────────┐
   ▼              ▼               ▼               ▼              ▼
 UNDERSTAND     PROPOSE        ACT SAFELY       VERIFY        REMEMBER
 things ·       rule miner ·   guardrails ·     read-back +   episodes ·
 moments ·      rule           scenarios ·      another       recall ·
 routines →     candidates     direct actions   device        lessons
 VIRTUAL
 SENSORS ★
 [todo]         [todo]         [started]        [started]     [todo]
   ▲              ▲               ▲               ▲              ▲
   └──────────────┴───────────────┼───────────────┴──────────────┘
                                  │
        AGENT (LangGraph): wake → reason → MCP tools → report           [done, v1]
                                  ▲
        WAKE: triggers · heartbeat · rules that act without the LLM     [done]
                                  ▲
        PERCEIVE: event loop · poll · diff · store (SQLite + JSONL)     [done]
                                  ▲
        CONNECTORS
          ├──► Altice MCP via mcp-remote ──► kit (Smart Home Beta)      [partly back]
          ├──► REPLAY of recordings [done] ──► SIMULATOR (fake MCP)     [todo]
          └──► Google Calendar: read plans · add events                 [idea]

 CONSOLE (console/): real device cards · virtual cards via Alfred       [real: done]
```
- **Capabilities exposed as tools:** any client can use them, and use cases sit on top without changing the core.
- **Connectors share one interface:** the simulator is a fake MCP with the same tools, so an offline kit only blocks going live.
- **Code decides when to think; the LLM decides what to do.** Triggers run every ~20 s, simple rules act without the LLM, and safety never waits for it.
- **Virtual sensors are readings like any other**, so diff, triggers and rules work on them unchanged.

## The path
```
 START
  │
  ①  Foundations & unblock ─────────────── ⛔ everything waits on this
  │
  ②  Connect to the home (real MCP + simulator) ── side tracks ───────────┐
  │                                                                       │  ⚪ define the use cases
  ③  Perceive + wake: changes → events → triggers                         │  ⚪ chase organisers & mentors
  │                                                                       │  ⚪ approach report
  ④  Understand → virtual sensors   ★                                     │  ⚪ language prototype
  │                                                                       │     on fake data
  ⑤  Propose: rules from routines                                         │  ⚪ calendar connector
  │                                                                       │
  ⑥  Act safely: guardrails → scenarios / actions ── ⛔ real scenarios    │
  │                                                     wait on mentors   │
  ⑦  Verify: did it actually work?                                        │
  │                                                                       │
  ⑧  Remember: episodes → better responses next time                      │
  │                                                                       │
  ⑨  Talk: ask · tell · teach · why  ◄────────────────────────────────────┘
  │
  ⑩  Expose: Alfred as tools / its own MCP server   ★★ PRODUCT CORE DONE
  │
  ⑪  Showcase: chosen use cases on top · go live if the kit is back · demo & deliverables
 FINISH
```

### ① Foundations & unblock ⛔
- Agree the hand-off formats: home state & actions · event · meaning objects (moment, routine, rule, memory, virtual sensor, plan).
- Get the kit onto the platform and send the mentor questions (scenario format, delete, rate limits).
- *Done when:* the formats are written down and the questions are sent.

### ② Connect to the home
- Real MCP + simulator behind the same interface. The simulator is a **fake MCP server with the same tools**, so writes work without the kit too.
- It generates **generic household activity** over weeks (habits, variations, the odd anomaly), replayable at any speed.
- *Done when:* the product can't tell the simulator from the real MCP.

### ③ Perceive + wake
- Poll, diff and store history (the MCP keeps none). Triggers are data: a heartbeat to catch absence, locked safety triggers, and rules that act without the LLM.
- *Done when:* every change is a stored event, with "who did it" inferred (Alfred, a scenario or a person).

### ④ Understand → virtual sensors ★
- **Things** (what each device is), **moments** (events that mean an activity), **routines** (repeated moments, with windows and confidence).
- **Shown as virtual sensors:** a sentence → a declarative spec → one evaluator that fills in the history and runs live → readings like any device. A moment is an event sensor, a state is an on/off sensor, a routine is a trend. Details: [`use-cases.md`](use-cases.md), V. Sensors that don't exist.
- *Done when:* a sentence creates a sensor with its history on the simulated weeks, and one runs live on the kit.

### ⑤ Propose
- Rule miner: routines, deviations and virtual sensors → **rule candidates** in plain words, with evidence.
- *Done when:* sensible rules come out of the simulated data, each with its "why".

### ⑥ Act safely
- Guardrails: approval tiers ("off is safe, on needs a human"), audit log.
- Approved rules become **MCP scenarios** (registry + versions; no edit or delete) or **Alfred rules** (trigger actions, virtual-sensor triggers, one-off calendar plans). Safety never waits for the AI.
- ⛔ Real scenarios wait on the mentors; until then, simulator only.
- *Done when:* approving a rule creates a scenario, changing it creates a v2, and nothing acts without the right approval.

### ⑦ Verify
- After every action, read the device back, then check **another device** for the effect. A deterministic action ledger reports confirmed / partial / failed ([`event-loop.md`](event-loop.md) §7).
- *Done when:* a failed or offline device is caught instead of reported as success.

### ⑧ Remember
- Record each episode (situation → response → outcome → lesson) and recall similar ones. Repeated choices feed ⑤.
- *Done when:* a repeated situation gets a better response than the first time, with the reason shown.

### ⑨ Talk
- **ask** · **tell** (create or change a rule) · **teach** (correct a meaning or a sensor) · **why** · **invent** (*"a sensor that knows when Rosa is asleep"*) · **plan** (*"friends for dinner on Friday"*).
- *Done when:* each one works against the real engine.

### ⑩ Expose ★★ product core done
- Every capability as a tool on Alfred's own MCP server (FastMCP): `what_happened`, `list_sensors`, `create_sensor`, `propose_rules`, `verify`, `recall`, `explain`…
- A small version comes early: the console's virtual cards read `list_sensors` and `sensor_history` from it.
- *Done when:* a generic MCP client (e.g. Claude) can use Alfred end to end.

### ⑪ Showcase
- V, A and E on top of the core ([`use-cases.md`](use-cases.md)), with the console as the thin client.
- Live where the kit allows; otherwise the simulator plus recorded MCP calls.
- Deadlines: **approach report Wed 18 Nov, 19:00** · **final submission Fri 20 Nov, 17:30** ([`deliverables/README.md`](../deliverables/README.md)).

### 📅 Calendar: plans as context (side track)
Google Calendar first, and a fake calendar (JSON) in the simulator. It gives Alfred **plans** (what people intend) next to moments (what happened).

| Plugs into | What it adds |
|---|---|
| ① formats | A **plan**: when, until, kind (guests, away, trip, early start) |
| ③ wake | "Wake me 15 min before the dinner": an agent-made trigger with an expiry |
| ⑤ propose | A **setup card** per plan; it asks how people want the house when there's no known preference |
| ⑥ act | One-off plans on Alfred's scheduler, recurring ones as an MCP schedule scenario; tiers unchanged |
| ⑨ talk | *"Friends for dinner on Friday at eight"* → the event (read back before saving) + its setup card |

## Blockers outside our control
| Blocker | Status | Workaround |
|---|---|---|
| Kit back on the MCP | 🟡 Partly: re-paired in the Smart Home Beta app | Simulator + recorded MCP calls |
| Device IDs changed with re-pairing | 🟡 The contact sensor has a new ID; check the rest | Find devices by type |
| Scenario format unknown, no delete | Open | Simulator first; ask the mentors; one inactive test |
| No MCP tool to create a device | Open | Virtual sensors live in Alfred's console and MCP; pitch it as the integration Altice switches on |

## Rules of thumb
- **"Done" = works on the simulator.** Live is a layer on top.
- **Keep the core use-case-agnostic:** no personas or client logic in ②–⑩.
- **Sensors are declarative specs, never free code**, with one evaluator for history and live.
- **No autonomous write above tier 1** until ⑥'s guardrails exist. Only a small lamp on the real plug.
- **Find devices by type, never by hard-coded ID.**
- **Cut first:** extra devices, comfort/energy trade-offs, free-form sensor sentences beyond the demo set.

## Follow-ups
- [`mcp-capabilities.md`](mcp-capabilities.md): the "kit offline" banner and the device IDs.
- [`hardware-list.md`](reference/hardware-list.md): the open items and the "don't re-pair" rule.
