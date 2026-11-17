# SYNAPSE: the semantic layer for the connected home

> **Background only.** The earlier concept under the old name SYNAPSE. The build follows [`starting-plan.md`](starting-plan.md). Rosa is still our persona; rules, verification and memories are parked.

> **From "Plug: 2140 W" to "Rosa is making her morning tea".**

Concept for Altice Labs Desafio #3, checked against the real MCP ([`mcp-capabilities.md`](mcp-capabilities.md)) and the judging criteria ([`altice-hackathon-brief.md`](altice-hackathon-brief.md)). It builds on the [strategy PDF](reference/strategy-ideas.pdf)'s plan (working name there: Casa Atenta).

## 1. The idea in one minute
Smart homes speak in **devices and values**: `Contact sensor: alarm`, `Plug: 2140 W`. People speak in **moments, places and habits**: "Mum's up", "tea time", "when I go to the market".
**SYNAPSE is the semantic layer in between.** It turns raw MCP state into meaning (*what things are, what just happened, what usually happens*), **detects routines**, **proposes rules from them**, **remembers what happened and what worked**, and lets people shape their home **just by talking**.
Altice keeps its devices, its MCP and its scenario engine. SYNAPSE adds understanding on top.

## 2. The story: "The house that learned Rosa"
**Rosa**, 81, lives alone in Ílhavo and is a MEO customer. **Ana**, her daughter, lives in Paris with SFR. Ana installs the Altice kit and SYNAPSE.

| Day | What the house sees (raw MCP) | What SYNAPSE understands | What happens |
|---|---|---|---|
| **0** | 7 devices called "Light", "Plug", "Contact sensor"… No rooms, no meaning | Nothing yet | SYNAPSE asks two questions out loud. *"What's on the plug?"* Rosa: *"My kettle."* *"Where's the door sensor?"* *"The front door."* |
| **2** | `Contact: alarm` 08:09 · `Plug: 2140 W` 08:21–08:24 | Front door opened → **"Rosa made her tea"** | Moments start to fill Rosa's day |
| **7** | A week of state changes | **Routines:** morning tea (08:05–08:30, 6 of 7 days) · bedtime (lamp off ~23:10) · Thursday market (out 10:00–12:00) | The house now knows what a normal day looks like |
| **8** | — | **Possible rules, from those routines** | Three cards, in Rosa's words: *"When you make your morning tea, shall I warm up your lamp?"* ✅ · *"If you haven't had your tea by 9:30, may I check on you and tell Ana?"* ✅ · *"On market Thursdays, shall I switch the plug off?"* ❌ |
| **12** | — | Rosa says: *"Let me sleep in on Sundays."* | SYNAPSE knows what "sleep in" means *for this rule*: the Sunday check moves to 10:30. A new version replaces the old one, explained first |
| **15** | Kettle on (2140 W, 08:19) · lamp unchanged, last seen 2 days ago | **"The tea rule fired, but the lamp didn't respond: it's offline."** Checked on a *different* device, not assumed | Instead of reporting success, SYNAPSE tells Ana: *"Mum's lamp hasn't responded since Tuesday. Probably unplugged."* Ana calls; Rosa had unplugged it. It's back by day 21 |
| **21** | 09:30: no door, no kettle | **"Morning tea hasn't happened. Unusual: 20 of the last 21 days it happened by 08:40."** | The lamp turns amber and the tablet asks *"Bom dia, Rosa! Está tudo bem?"*. No answer → Ana gets the reason, the evidence and three options: call · ask the neighbour · book a MEO Care visit. Ana asks the neighbour; Rosa just has a cold. **SYNAPSE keeps this as a memory** |
| **40** | 09:00: no door, no kettle, after a cold night (15 °C) | **"This happened before (day 21): a cold night, then no tea. Last time Rosa had a cold and the neighbour helped."** | It recalls the memory: checks in 30 min earlier and offers Ana *"ask the neighbour (worked last time)"* first. One tap, sorted in 10 minutes |

**The point:** the same seven devices tell Ana nothing on day 0. By day 21 they tell her exactly what she needs to know, and nobody had to write a single rule. By day 40 the house remembers what helped last time.
(Safety doesn't wait for learning: smoke always triggers the instant "SY · Fumo" scenario, see §10.)

## 3. The semantic layer: four levels of meaning
| Level | Question it answers | From | To | Example |
|---|---|---|---|---|
| **Things** | What is this, and where? | Device type + capabilities | Named things in places | Plug → *Rosa's kettle* · Contact → *front door* · Light → *Rosa's lamp* |
| **Moments** | What just happened? | State changes (who did them, when, in what order) | Human activities | Door 08:09 + 2140 W for 3 min → *"Rosa made her morning tea"* |
| **Routines** | What usually happens? | Moments across days | Habits with time windows and confidence | *Morning tea · 08:05–08:30 · 6 of 7 days* |
| **Rules** | What should happen? | Routines + what people say | Proposed, then approved, automations | *"If no morning tea by 9:30 → check on Rosa, then tell Ana"* |

One piece of data, climbing the levels:
```
RAW      Plug.instantPower = 2140 W (08:21–08:24) · Contact.sensorState = alarm (08:09)
THING    Plug = "Rosa's kettle" (kitchen) · Contact = "front door"
MOMENT   "Rosa made her morning tea" · 08:21
ROUTINE  "Morning tea" · 08:05–08:30 · 6 of 7 days · confidence 0.86
RULE     proposed: "If no morning tea by 09:30 → check on Rosa, then tell Ana"
```

**The home model** is a small graph of **people, places, things, moments, routines, rules, words and memories**. *Words* holds the household's own vocabulary ("Mum's lamp", "tea time", "market day") linked to the rest, so whatever people say lands on something real. *Memories* hold what happened before and what worked (§7).

## 4. How it infers details
| Detail | Signal | Method | Confirmed by |
|---|---|---|---|
| What's on the plug | Power signature | Kettle: 1.8–3 kW for 2–5 min. Heater: 1–2 kW for a long time. Fridge: ~100 W, cycling. TV/lamp: <150 W in the evening | One question, once |
| Door or window | What follows the opening | Temperature or humidity drifts → window. A quick open/close plus activity → door | One question, once |
| Someone home and active | Any change made by a person | Manual light change, plug use, door; or "Fora de casa" active → nobody home | Automatic |
| Who did it | Timing | SYNAPSE's own action / a scenario's action / otherwise a person | Automatic |
| Wake-up and bedtime | First and last activity of the day | Daily times → routine windows | Automatic |
| Comfort preferences | Light settings people choose by hand | "Warm, 60 % in the morning", turned into rule defaults | Shown in the rule card |
| Something unusual | Routine window + its spread | Not seen by the usual time + tolerance → a deviation, with evidence | Automatic, explained |

**How routines are found: simple, explainable, no LLM.** For each kind of moment, collect its time each day, then take the median and the spread. It counts as a routine when it shows up on **≥5 of 7 days** with a spread **under 30 min**. Confidence = how often × how tight. It's a deviation when it hasn't happened by **median + max(3 × spread, 45 min)**.
It works with what this kit has (door, climate, smoke, one plug, one light), and every extra device adds more meaning.

## 5. From routine to rule
```
 moments ──► routine detected ──► candidate rules ──► rule card (plain words + evidence)
                                                          │  Yes / Not now / Change…
                                                          ▼
                    MCP scenario (when it can be expressed)  or  SYNAPSE rule (when it can't)
                                                          │
                                                          ▼
                    observe: kept, undone, ignored? ──► adapt (new version, pause, suggest less)
```
| Routine shape | Rule it suggests | Runs where |
|---|---|---|
| Starts with a device event (kettle on) | "When X happens, do Y" (lamp warm 60 %) | **MCP scenario**, device trigger (`instantPower > 1500`) |
| Happens at a time (bedtime ~23:10) | "At 23:00, dim the lamp and switch the plug off" | **MCP scenario**, schedule trigger |
| Expected but missing (no tea by 9:30) | "Check on Rosa, then tell Ana" | **SYNAPSE** (scenarios can't express absence) |
| People undo an automation (lamp back on after bedtime) | A new, later or dimmer version, or a pause | Replaces the old scenario (v2) |
| Nobody home at a regular time (Thursdays) | "Switch the plug off when you're out" | MCP scenario, schedule |
| Two rules fight over one device | Merge them | Registry |
| The same situation keeps getting the same human choice (≥3 times) | Make that choice the default response | MCP scenario or SYNAPSE rule, from memories (§7) |

**Rule card** (what Rosa or Ana sees)
> **When you make your morning tea, shall I warm up your lamp?**
> *Why:* you made tea between 8:05 and 8:30 on 6 of the last 7 days, and you usually set the lamp to warm.
> *It will:* set the lamp to warm, 60 %, when the kettle starts.  **[Yes]  [Not now]  [Change…]**

**The same rule as an MCP scenario** (`post_new_scenario`. The `triggerLogic` and `operator` formats are still to confirm)
```jsonc
{
  "name": "SY · Chá da manhã · v1", "isActive": false, "isPreset": false, "triggerLogic": "OR",
  "triggers": [{ "type": "device", "device": { "id": "a291277c-8ba5-480a-b3a3-35b5f49bb459" },
                 "capabilities": [{ "name": "", "type": "instantPower", "operator": ">", "value": 1500 }] }],
  "startActions": [{ "type": "device", "device": { "id": "90654832-7bca-47dd-89f3-d6e0b5f0a9ee" },
                     "capabilities": [{ "name": "", "type": "power", "value": "on" },
                                      { "name": "", "type": "temperature", "value": 20 },
                                      { "name": "", "type": "brightness", "value": 60 }] }],
  "endActions": []
}
```
**Living without edit or delete:** SYNAPSE keeps its own **rule registry**. "Edit" = create `v2` + deactivate `v1`. "Delete" = deactivate + hide. Names follow `SY · <name> · v<n>`. New scenarios are created inactive, checked, then activated.

## 6. Verification loop: did it actually work?
An action isn't done when the API says "OK". It's done when **the home shows it**. SYNAPSE checks every action, first on the device itself, then on **another device** that should feel the effect.
```
 act ──► predict (which device, what change, by when) ──► observe ──► compare
                                                                         │
          ┌────────────── confirmed / partial / failed ◄─────────────────┘
          ▼
  learn (cause → effect)  ·  explain ("Porquê?")  ·  fall back or escalate
```
| Level | Question | How it's checked |
|---|---|---|
| 1. Accepted | Did the MCP take the command? | Tool result, not `SH_10500` |
| 2. Applied | Did the device change? | Read it back: new value **and** a newer `lastCommunicationTime` |
| 3. Effective | Did the world change? | **Another device** shows the expected effect |
| 4. Achieved | Did the person get what they wanted? | Goal met: Rosa replied, the room dried, the routine came back |

| Action | Checked on | Expected | If not |
|---|---|---|---|
| Plug off (e.g. a heater) | Plug power → temperature sensor | ~0 W within a minute; temperature stops rising within 20 min | Still drawing power → tell Rosa or Ana; the plug may be stuck |
| Plug on (e.g. a dehumidifier) | Plug power → humidity | >150 W within a minute; humidity down ≥3 points in 45 min | Humidity flat and the window open (contact) → *"Close the window, it's working against the dehumidifier"* |
| "Close the window" suggestion | Contact → temperature and humidity | Contact back to normal; readings settle | Still open after 15 min → remind once, then tell Ana if it's cold |
| Lamp amber (check-in) | Lamp read-back → door and kettle | Lamp matches; any activity from Rosa within 10 min = she's OK | No activity → Ana gets the options (tier 4) |
| "Chá da manhã" scenario | Kettle (trigger) → lamp (effect) | Lamp warm within one poll after the kettle starts | SYNAPSE sets the lamp itself; if the lamp doesn't answer → "lamp offline" (day 15) |
| "SY · Fumo" scenario | Lamp + plug read-back | Lamp red, plug ~0 W within one poll | SYNAPSE sends the actions directly, alerts Ana, logs "scenario didn't fire" |
| Scenario created or switched | `get_home_scenarios` | Listed with the right `isActive` | Retry once, then mark it "not saved" in the registry |
| Alarm mode (tier 4) | `get_home_security_status` | New status | Tell the person immediately; never assume |

**The LLM's job in the loop:** before acting, it writes the check as a **prediction** ("plug under 5 W within 60 s; bedroom stops warming within 20 min"). A deterministic checker compares that with what the devices report. The LLM then explains the result. It never says "done" below level 2; otherwise it says *"I tried, but couldn't confirm"*.

**Verification also teaches the semantic layer:**
- *Heater on → this sensor rises 1 °C in 20 min* → the heater and the sensor are in the same room (a place inferred).
- *Opening the contact lowers humidity* → it's a window, not a door.
- Every confirmed effect raises confidence in the home model. Every failed one lowers it and may prompt a question.

*Real case from our kit (16 Nov): a colour command to the lamp returned `SH_10500`, and its `lastCommunicationTime` didn't move. SYNAPSE would report "couldn't reach the lamp, offline since 14 Nov" instead of claiming success.*

## 7. Memories: the house remembers what happened and what worked
Every time something happens and SYNAPSE responds, it keeps a **memory** of the whole episode. When a similar situation comes back, it remembers and does better.
```
 situation ──► recall similar memories ──► respond (reuse what worked) ──► verify (§6)
     ▲                                                                       │
     └──────────── remember: situation · response · outcome · lesson ◄──────┘
```
**A memory record**
```
MEMORY #14 · day 21 · 09:30
situation  morning tea missing · after a cold night (15 °C) · no active scenario explains it
did        lamp amber (✓ verified) · tablet check-in (no reply in 10 min) · told Ana
people     Ana chose "ask the neighbour" · Rosa had a cold, all fine at 10:05
outcome    resolved in 35 min · no service needed
lesson     tea missing after a cold night → check in earlier; offer "neighbour" first
```
| Happening | What SYNAPSE did the first time | What it does next time |
|---|---|---|
| No morning tea after a cold night (day 21) | Amber lamp, tablet, then Ana; the neighbour helped | Checks in 30 min earlier and offers "neighbour" first (day 40) |
| Lamp offline on a Friday (day 15) | Told Ana; Rosa had unplugged it while cleaning | Asks Rosa first (*"Unplugged while cleaning again?"*) and tells Ana only if there's no answer |
| Dehumidifier on, humidity flat, window open | Waited 45 min, then said "close the window"; Rosa closed it within 5 min | Says it straight away |
| Door opens at 3:00 | Asked Rosa; she was getting the post | Soft light only, logged; asks again only if the time or pattern is different |
| Smoke at dinner time; Rosa says "just toast" | Instant red lamp + plug off; Ana alerted | **The instant response is unchanged** (safety never waits). Then it asks Rosa *"Toast again?"* before alarming Ana, and escalates if she doesn't reply within 1 min |

**How memories behave**
- **Recall by similarity:** the same kind of situation in a similar context (time, day, temperature, active scenarios, who's home).
- **Strength:** verified successes and repeated choices strengthen a memory. Failures, corrections and *"forget that"* weaken or delete it. A single episode only *suggests*; repeated ones change the default.
- **Memories become rules:** the same situation with the same choice ≥3 times → a rule card (§5).
- **Always explained:** every recall says so, e.g. *"Last time (day 21) the neighbour went round and all was fine."*
- **Guardrails:** memories can speed things up and pre-select options, but **never skip an approval tier** and **never silence the safety path** (§10). People can see, correct and delete any memory, and memories stay in the EU.

## 8. Just talk: how interaction changes
| Today (device language) | With SYNAPSE (people's language) |
|---|---|
| App → Devices → Light → colour picker | *"Make Mum's lamp cosy."* |
| Scenario editor: `Contact sensorState = alarm` AND 23:00–07:00 → `Light color_rgb …` | *"If the front door opens at night, give Mum a soft light."* |
| Push notification: "Contact sensor: alarm" | *"The front door opened at 3:10. Rosa doesn't usually go out at night."* |
| Status screen: "Plug 0 W" | *"Rosa hasn't had her tea yet."* |
| Writing every rule by hand | The house **suggests** rules from routines; you say yes |

What talking can do:
- **Ask:** *"Is Mum OK?"* → an answer built from today's moments versus her routine.
- **Tell:** *"Let me sleep in on Sundays."* → SYNAPSE finds which rules this touches and shows the change before saving it.
- **Teach:** *"That's not the kettle, it's the heater."* → the thing is renamed, and its moments and rules are rebuilt.
- **Question:** *"Why did the lamp turn amber?"* → **"Porquê?"**: the evidence, the rule, and who approved it.
- **Check:** *"Did the heater go off?"* → *"Yes: the plug dropped to 0 W at 21:02, and the bedroom stopped warming."*
- **Remember / forget:** *"What did we do last time?"* → the memory, in plain words. *"Forget that."* → it's gone.

The house also **talks back to learn**. It asks only when an answer unlocks meaning ("Is that your kettle?"), and asks less when people ignore it.

## 9. How SYNAPSE works
```
 Altice kit ◄──► connected-home MCP ◄─────────── actions: lamp, plug, scenarios
                        │ poll every 30–60 s                      ▲
                        ▼                                         │
 ┌──────────────────── SYNAPSE (itself an MCP server) ────────────┴───────────┐
 │  Event bridge + history ──► SEMANTIC LAYER: things · moments · routines ·  │
 │                             rules · words · memories (+ simulator)         │
 │        │                                                                   │
 │        ▼                                                                   │
 │  Rule miner ──► Agent (LLM: words ↔ model) ──► Guardrails ──► actions      │
 │        └──── safety path: rules act at once, no LLM ────►┘                 │
 │  Verifier: read-back + other devices ◄── after every action                │
 │        └──► Memory store: situation · response · outcome · lesson          │
 │                  └──► recalled by the agent next time it happens           │
 └────────────────────────────────────────────────────────────────────────────┘
   in: Rosa's voice, Ana's app, profile, tariff        out: tablet · Ana's app · MEO services
```
| Block | Job |
|---|---|
| Event bridge + history | Polls the MCP, diffs states, stores every change (the MCP keeps no history) |
| Semantic layer | The home model: things, moments, routines, rules, words |
| Digital twin / simulator | Previews rules before they go live; replays weeks; stands in while the kit is offline |
| Rule miner | Finds routines and turns them into candidate rules (deterministic) |
| Agent (LLM) | Translates between people's words and the model; writes rule cards and explanations. Runs only when needed |
| Guardrails | Approval tiers, audit log, "Porquê?" |
| Verifier | Checks each action by read-back and on another device; feeds results back into the model (§6) |
| Memory store | Keeps every episode (situation, response, outcome, lesson) and recalls similar ones (§7) |

**SYNAPSE is itself an MCP server**, so MyButler or any other agent gets *understanding*, not just devices: `what_happened(since)`, `is_anyone_home()`, `get_routines()`, `propose_rules()`, `explain(event)`, `verify(action)`, `recall(situation)`.

## 10. Guardrails
| Tier | What | Who decides |
|---|---|---|
| 0 | Learn, explain, summarise | Automatic |
| 1 | Safe and reversible: lamp changes, plug **off** | Automatic, logged |
| 2 | Adds load or cost: plug **on** | The person at home, one tap |
| 3 | New or changed rules / scenarios | Rule card + explicit yes |
| 4 | Alarm modes, calling family / 112 / services | Always a human |

**Off is safe, on needs a human.** **Safety never waits for learning:** "SY · Fumo" (smoke → lamp red at 100 %, plug off) is approved at setup and runs on Altice's server instantly. **Nothing counts as done until it's verified** (§6), and a failed safety action escalates at once. Memories (§7) can pre-select options, but never skip a tier or silence the safety path. Every action is logged with its evidence, approval and verification result, and answers "Porquê?".

## 11. MCP fit check
| Need | MCP | Status → workaround |
|---|---|---|
| Read devices and state | `get_home`, `get_home_devices` | ✅ |
| Notice changes / history | none | ❌ → poll every 30–60 s, our own history |
| Rooms and names for things | `get_home_rooms` is empty; no rename tool | ❌ → the semantic layer holds places and names |
| Act on the lamp and plug | `update_device_capabilities` | ⚠ only `power`, `temperature`, `brightness`, `color_rgb`; kit live since 17 Nov, lamp/plug writes not re-tested yet → simulator as fallback |
| Create / switch on scenarios | `post_new_scenario`, `patch_scenario_activation` | ⚠ format untested → ask mentors, one inactive test |
| Edit / delete scenarios | none | ❌ → registry with versions |
| Speak to Rosa | none (no camera speaker) | ❌ → text-to-speech on her tablet |
| Alarm | `get_home_security_*`, `patch_home_security_status` | ✅ read / ⚠ write (tier 4 only) |
| Verify actions | `get_device_details` read-back, `lastCommunicationTime`, other sensors, `get_home_scenarios` | ✅ reads work today |

## 12. Why it scores
| Criterion (final weight) | How SYNAPSE hits it |
|---|---|
| Fit (20 %) | Exactly what the brief asks for: **context + intent + information + action**, not commands. The house understands, reasons, suggests, and **learns from experience** (memories) |
| Altice adoption (20 %) | A layer on the kit and MCP Altice already have. It feeds **their** scenario engine, matches their roadmap of using generative AI to write scenarios, and gives MyButler understanding through MCP |
| Feasibility (20 %) | Routines come from simple statistics; the LLM is only used for language. Every action is **verified on a second device**, so the system knows when it fails. A simulator covers the offline kit |
| Replicability (20 %) | Any home, any devices, any language: the model learns each home instead of being configured. Multi-operator (MEO + SFR) |
| Presentation (10 %) | One clear arc: day 0 → day 21, raw data → meaning |
| Documentation (10 %) | This doc, the MCP reference, and the audit log |

## 13. Business
- **An add-on at about €3–5/month** (SFR's Maison Sécurisée costs €14.99), plus the services it leads to: **MEO Care** visits, **MEO Energia** tariff advice, **Europ Assistance** in France.
- **Lower costs:** fewer false alarms (they're judged against routines) and fewer support calls (the house explains itself, and verification spots broken or offline devices before the customer does).
- **A platform asset:** the semantic layer makes every Altice device and partner app smarter, including MyButler. Sellable to other operators.
- **Why now:** 37.8 % of people aged 80+ in Portugal lived alone (2021). Generic AI summaries (Google's Gemini for Home) are becoming a commodity, while a model that learns each home and ends in operator services is something only an operator can offer.

## 14. Demo (5 minutes)
| Time | Moment |
|---|---|
| 0:00–0:40 | **The gap:** raw MCP JSON on screen (`Plug 2140 W`, `Contact: alarm`) next to Ana's real question: "Is Mum OK?" |
| 0:40–1:40 | **21 days in 60 seconds:** the simulator replays Rosa's weeks while the layers fill up live: things get names, moments appear, routines form with confidence bars |
| 1:40–2:30 | **Rules from routines:** three rule cards; we approve two → they appear as scenarios in `get_home_scenarios` |
| 2:30–3:15 | **Just talk:** "Let me sleep in on Sundays" → SYNAPSE shows which rule changes (v2) and why |
| 3:15–4:05 | **Day 21 + verification panel:** no tea → the real lamp turns amber (✓ read back), the tablet asks, no door or kettle within 10 min (✗ checked on other devices) → Ana gets the evidence + a MEO Care option |
| 4:05–4:30 | **Day 40, it happens again:** the house recalls memory #14 → checks in earlier and offers "neighbour (worked last time)" |
| 4:30–5:00 | Architecture, "MyButler gets understanding through SYNAPSE's MCP", business |

Fallback: the whole demo runs on the simulator with the same tool calls. When the kit is live, a side panel shows the real MCP calls.

## 15. Build plan
| When | Goal |
|---|---|
| Mon 16 (tonight) | Ask the organisers about the kit; lock the story |
| Tue 17 | Event bridge + history; simulator with a **synthetic 21-day "Rosa" dataset** |
| Wed 18 (mentor checkpoint; **report due 19:00**) | Semantic layer v1 (things + moments); 2-page report; ask about the scenario format and deleting |
| Thu 19 | Routine detection + rule miner + rule cards → scenarios / registry; "just talk" (LLM ↔ model); verifier (read-back + cross-device checks); memory store (record + recall) |
| Fri 20 (coding 09:30–16:30; **submit 17:30**) | Ana's app + Rosa's tablet; amber check-in; "Porquê?" log; rehearse; summary + slides |

**MVP:** history, simulator, things + moments + routines, three rule types (device trigger, schedule, absence), rule cards → scenarios, "Let me sleep in on Sundays", verification loop (levels 1–3, at least one cross-device check), memories (record + recall by situation type, for the day-40 replay), day-21 check-in, safety scenario.
**Stretch:** SYNAPSE MCP server for MyButler; comfort-preference learning; French for Ana; IPMA weather.
**Team of 4:** data (bridge, history, simulator) · semantics (things, moments, routines, rule miner) · language (LLM, rule cards, explanations) · front-end & pitch.

## 16. Risks and questions
| Risk | Mitigation |
|---|---|
| Not enough real data to learn from | A synthetic 21-day dataset for the demo; log everything as soon as the kit is live |
| Wrong inferences ("that's not the kettle") | Ask once, accept corrections by voice, show confidence, never act on low confidence |
| Scenario format unknown, no delete | Ask mentors first; one inactive test; registry versioning |
| The kit stays offline | Simulator with identical tool calls |
| Physical effects are slow or noisy (temperature, humidity) | A time window and threshold per check; report "partial" honestly instead of guessing |
| Learns the wrong lesson from one episode | One episode only suggests; behaviour changes only after repeats with verified outcomes; memories are visible and correctable |
| "Elderly care" is a crowded theme | Lead with the **semantic layer** (any home, any person); Rosa is the story, not the product |
| Privacy | No cameras, only meanings shared with family (never raw data), data in the EU, memories deletable ("forget that") |

**Questions for the mentors:** scenario `triggerLogic` / schedule / `operator` format? Can test scenarios be deleted? Is the kit online and ours alone? Can the platform speak or notify? Any rate limits?
