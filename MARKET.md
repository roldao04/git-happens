# Alfred: market

> **In short.** Smart homes have moved from switches to assistants. Voice control, remote apps and routines you write yourself are now standard. What's missing is a home that **learns its own household by itself**, with no setup, and **acts ahead** of people, with a confidence they can see and overrule. That is what Alfred proposes.

## What we propose
- **It learns by itself.** It reads every device together, with no rules and no configuration.
- **It knows each person and each kind of day.** Ana's arrival and João's late Tuesdays are separate rhythms, and Alfred recognises a party as a party, not a broken routine.
- **It acts ahead, only when sure.** The house is warm before you get home. It acts at confidence ≥ 0.75 and only suggests below that.
- **It stays in your hands.** It says what it did and why, you can undo it, and its core works with no LLM at all.

## How the market compares
| | Alfred | Amazon Alexa | Apple Home | Learning thermostats | Home Assistant |
|---|---|---|---|---|---|
| Learns habits by itself | ✓ | ✓ | – | ✓ | – |
| Knows who is home | ✓ | ✓ | ✓ | ✓ | ✓ |
| Each person's own routine | ✓ | – | – | – | – |
| Acts ahead of you | ✓ | – | – | ✓ | – |
| Spots the unusual | ✓ | ✓ | – | – | – |
| Asks when unsure | ✓ | ✓ | – | – | – |

✓ yes · – no.

## Where we win
1. **Zero setup, whole home, each person.** Others learn one device at a time (Alexa, thermostats) or run the rules you give them. Alfred learns the whole household from all its devices together, with a routine for each person.
2. **Anticipation you can trust.** Every action has a confidence computed from the home's own history. Alfred acts above 0.75, suggests below it, and always yields to a person.
3. **It understands exceptions.** A Friday party moves tomorrow's wake-up instead of setting off a missed-routine alert.
4. **Cheap and private by design.** Detection is statistics on the home's own data. The LLM is called only when something happens, not on every reading, so a small model costs under €1 per home a month, and it can also run EU-hosted or self-hosted.

## Where we are behind
- **A prototype.** It is proven on simulated households; a real home needs a few weeks of history before Alfred acts.
- **No ecosystem.** The big players have voice speakers, millions of homes and partner devices.
