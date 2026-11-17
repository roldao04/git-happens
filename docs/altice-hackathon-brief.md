# Aveiro Tech City Hackathon 2026 — Altice Labs Challenge #3: Key Info

Sources: [challenge sheet](reference/challenge-sheet.pdf), [FAQs/rules](reference/faqs.pdf), [Altice hardware list](reference/hardware-list.md). Saved 13 Nov 2026.

## At a glance
- **Challenge:** Desafio #3 by Altice Labs — *Connected Home, Reimagined: A IA ao serviço da casa*
- **Format:** hybrid. Online 16–18 Nov, in person 20 Nov (mandatory physical attendance)
- **Venue (20 Nov):** Fábrica Centro de Ciência Viva de Aveiro, 08h30–20h00
- **Team size:** 3–5 people
- **Main tech resource:** an MCP (Model Context Protocol) server for interacting with the Connected Home devices, plus a technical support document. **It must be integrated into the solution.**
- **Contact:** aveirotechcity@cm-aveiro.pt · https://techweek.aveirotechcity.pt/events/aveiro-tech-city-hackathon

## Schedule
| When | What |
|---|---|
| Fri 13 Nov | Challenge/team assignment announced |
| **Mon 16 Nov, 17h30** | Online opening session (MCP access starts; chance to meet mentors) |
| Wed 18 Nov, afternoon | **Mandatory online mentor checkpoint** (book the time with mentors, ideally at the 16 Nov session) |
| **Wed 18 Nov, 19h00** | **Deliverable 1 due:** approach report, max 2 pages |
| Fri 20 Nov, 08h30 | Arrival / registration |
| 09h00 | Opening and welcome |
| 09h30 | Coding time |
| 16h30 | Finalists selected |
| **17h30** | **Final submission:** executive summary (PDF, max 2 pages) + presentation (PPT) + code repo link (optional). Coffee break |
| 18h00 | Project presentations |
| 20h00 | Winners announced + cocktail |

The FAQ suggests at least 4–5 hours per person during the online phase. The organisers will provide **templates for all deliverables**, so use them.

## The challenge (Desafio #3)
- It's deliberately open. There is no set problem and no predefined type of app. The goal is to imagine new experiences, services and ways of interacting that become possible when AI can **understand, reason about and act on** the home's devices and surroundings.
- **Key line:** "it's not just about controlling devices through AI." Go beyond commands and combine **context, intention, information and action** into something useful, surprising or innovative.
- Suggested areas: automation, accessibility, energy efficiency, comfort, security, entertainment, communication, daily routines, or unexpected use cases.
- Possible forms: a practical app, a new way of interacting, a smart assistant, an autonomous workflow, or something that challenges how we think about connected homes.
- Allowed technologies: Mobile, Web, Cloud, AI.
- Their closing question: *"The house is already connected. What will you make it capable of doing?"*

## Hardware in the Connected Home (from Altice)
| # | Device | Model | Capabilities | Connectivity |
|---|---|---|---|---|
| 1 | IP camera | Nivian Smart IP Camera | 1080p, night vision, mic, speaker, microSD | Wi-Fi 2.4 GHz |
| 2 | Climate sensor | Nedis ZBSC10WT | Temperature + humidity | Zigbee |
| 3 | Door/window sensor | Home Sécurité 852524 | Open/close detection | Unknown |
| 4 | Smart plug | Altice F3S401-NF | 16A, on/off, scheduling, countdown, **power consumption** | Wi-Fi |
| 5 | RGB smart bulb | Altice A60 9W RGB | E27, RGB lighting | Unknown |
| 6 | Smoke alarm | ARIZA S100A-AAA-W | Photoelectric smoke detection | Wi-Fi + 868 MHz (to verify) |
| 7 | Security gateway | Altice MS-WES-01 (852441) | Hub; 3.7 V 5000 mAh backup battery | Wi-Fi 2.4 GHz, Ethernet, LTE/4G, 868 MHz radio |

> Corrected models and radios, how each device reaches the MCP, and safety rules: [`reference/hardware-list.md`](reference/hardware-list.md).

**Sensors (inputs):** camera (video/audio), temperature/humidity, door/window, smoke, plug power draw.
**Actuators (outputs):** smart plug, RGB bulb, camera speaker, gateway (probably alarm/siren features, to confirm).

## Evaluation
**Intermediate evaluation** (picks up to 6 finalists per challenge). Every team gets a 3-min pitch + 1 min Q&A to mentors/jury. All teams should prepare a pitch, because finalists are only revealed when they're called on stage.

| Criterion | Weight |
|---|---|
| Fit with and response to the challenge | 25% |
| Ease of adoption by the challenge owner (Altice) | 25% |
| Feasibility and future sustainability | 25% |
| Presentation | 25% |

**Final evaluation** (finalists only): 5-min pitch + 3 min Q&A.

| Criterion | Weight |
|---|---|
| Fit with and response to the challenge | 20% |
| Ease of adoption by Altice | 20% |
| Feasibility and future sustainability | 20% |
| **Replicability in other territories/contexts** | 20% |
| Presentation | 10% |
| Documentation (deliverables) | 10% |

Each criterion is scored 1–10. "Ease of adoption by Altice" and "feasibility" carry a lot of weight in both rounds, so the solution should plausibly fit Altice's real product line (MEO / smart-home offering).

## Rules worth remembering
- **AI tools are allowed** (coding assistants, content generation, etc.), but the jury may question technical choices and the team must show it really understands the solution. Using AI counts neither for nor against you.
- **Originality is required:** no full or partial copies of existing projects.
- **Intellectual property goes to the challenge owner (Altice Labs)**, for non-commercial use and adaptation, with an obligation to share.
- **Conflict of interest:** you can't compete on a challenge from an entity you're directly tied to. UA people can't do the UA challenge (#2), but Altice #3 is fine for UA members (unless someone on the team works at Altice Labs).
- Unjustified no-shows can affect eligibility for future editions.
- Bring laptops and any equipment you need. Lunch, coffee breaks and drinks are provided. Report dietary restrictions to aveirotechcity@cm-aveiro.pt ahead of time.

## Open questions to settle on Monday (when the MCP arrives)
> Answered on 16 Nov in [`mcp-capabilities.md`](mcp-capabilities.md) (what the MCP can see, change and can't do).

- Which MCP tools/resources exist: read-only state, events/subscriptions, or commands? Is there history (e.g., power consumption over time)?
- Can we get camera access (stream, snapshots, audio) through the MCP?
- What connectivity do the door/window sensor and bulb use, and does the smoke alarm run over Wi-Fi or 868 MHz? The climate sensor is Zigbee, but the gateway lists 868 MHz radio, so which hub bridges the Zigbee sensor?
- Is the house physical (live devices) or simulated, and is it shared between teams (risk of concurrent commands)?
- Rate limits, auth, and whether it works from the cloud and from our laptops.
- Is the deliverable template for the 18 Nov report already available?
