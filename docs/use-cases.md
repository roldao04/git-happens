# Alfred: use cases

> **Updated 17 Nov:** V now ships as **asked** sensors, a third origin next to *defined* and *inferred* in [`learned-sensors-plan.md`](learned-sensors-plan.md) (task D5 in [`build-tree.md`](build-tree.md)). A and E are stretch (F3).

What we show on top of the core ([`build-path.md`](build-path.md)), within what the kit and the MCP allow ([`mcp-capabilities.md`](mcp-capabilities.md), [`hardware-list.md`](reference/hardware-list.md)). The concept is in [`synapse.md`](synapse.md).

**Lineup:** V leads, A and E support, and B, C and D come if there's time. All of them run on the same core.

| Use case | Role | Fit | Altice value | Feasible | Wow | Live? |
|---|---|---|---|---|---|---|
| **V. Sensors that don't exist** | Flagship | 5 | 5 | 4 (needs the simulated weeks) | 5 | Partly: door and temperature sensors live, kettle simulated |
| **A. Habits become automations** | Supporting | 5 | 5 | 4 (3 if the scenario format fails) | 5 | Yes: bulb, plug lamp, door |
| **E. The house follows your plans** | Supporting | 5 | 4 | 4 | 5 | Calendar yes; devices once the kit is back |
| B. "Is everything normal?" | If time | 5 | 5 | 3 | 4 | One deviation live |
| C. Alerts that understand context | If time | 4 | 5 | 4 | 5 | Once the kit is back |
| D. The house checks itself | If time · opener | 3 | 4 | 5 (works today) | 3 | Yes |

## V. Sensors that don't exist (flagship)
- **Demo:** *"A sensor that knows when Rosa is asleep."* Seconds later, an 8th card appears next to the 7 real ones: **Sleep sensor · asleep since 23:12**, with 3 weeks of history already filled in. Do it again, and the home goes from 7 devices to 12.
- **How:** the LLM turns the sentence into a small declarative spec (devices, time windows, thresholds; never code). One evaluator fills in the history from stored events and runs the spec live on every poll. The card shows the value, the history and the definition in plain words. Alfred's MCP serves it like any other device.
- **Demo set:** rest · someone home · room being aired · morning as usual (B) · kettle getting slower (simulator only: the real plug carries only a small lamp).
- **Can't build it?** It says what it would need: *"I'd need a motion sensor in the bedroom."* That's a hardware sale for Altice.
- **Why Altice cares:** sensors with no hardware, stock, installer or returns, sold as a catalogue (illustratively ~€1/month each). MyButler or any agent reads "Rosa is asleep" as an ordinary device state.
- **Limits:** the Altice MCP can't create devices, so virtual sensors live in Alfred's console and MCP; we pitch that as the integration Altice switches on. "Asleep" really means "quiet since the lights went out", so every card shows its confidence.
- **Prior art (quick search; recheck before the pitch):** Home Assistant template sensors are hand-written, and Google Home's "Help me create" turns a sentence into an automation, not a sensor. We found nothing that builds a sensor from a sentence and fills in its history.

## A. Habits become automations (supporting)
- **Sees:** the same manual actions at similar times (on weekday evenings, the door opens → the bulb goes warm at 60 % and the plug lamp comes on).
- **Does:** routine → a rule in plain words, with evidence → after a yes, an MCP scenario (registry + versions) → verified when it fires. Overrides lead to a v2 (*"make it dimmer on weekends"*).
- **New:** triggers can be virtual sensors (*"when Rosa is asleep, switch the plug lamp off"*). Those rules run on Alfred's own rule engine, because Altice scenarios only trigger on real devices.
- **Risk:** the scenario format is unconfirmed → simulator first, then one inactive real test.

## E. The house follows your plans (supporting)
- **Tell:** *"Friends for dinner on Friday at eight"* → Alfred reads the event back and adds it to Google Calendar → *"How would you like the house for them?"* → a setup card → 15 min before, the lamp and plug lamp are set and verified.
- **Read and adapt:** a trip → an "away" setup, and a door opening during the trip is an alert. An early appointment → the "no activity" check waits.
- **Learn:** it remembers how people want the house for each kind of plan, and proposes that next time.
- **Risk:** privacy and Google sign-in → a demo account, only the calendars people pick, meanings rather than raw text. One-off events run on Alfred's scheduler, because Altice scenarios can't be deleted.

## Further use cases (if time)
- **B. "Is everything normal?"** Expected activity that didn't happen, or activity at an odd time → evidence, an amber light cue, then a contact. It ships as a V sensor ("morning as usual") and needs the simulated weeks.
- **C. Alerts that understand context.** Smoke → the instant safety scenario, verified, then a message that fits the context. The door while "away" → an alert; the door at a normal time → nothing.
- **D. The house checks itself.** Batteries, signal, offline devices and failed actions, explained before the customer calls support. It works on today's data, so it can open the demo.
- **Not now:** energy coach and comfort with appliances (no real load on the plug), presence simulation (little AI in it), anything camera-based (the MCP has no video).

## Build order
V (after ④ and a small ⑩) → A (after ⑤–⑦) → E (calendar connector + "tell") → D, B, C if there's time.
