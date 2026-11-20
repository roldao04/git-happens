# Alfred: business model

> **In short.** Alfred is sold through Altice as an add-on to the Connected Home offer: no new hardware, no new app. Families get **one month free** while Alfred learns them, then about **€4 a month**. Because the LLM is called only when something happens, it costs **under €1 per home per month** at launch.

## Go-to-market
| Phase | Who | What |
|---|---|---|
| **1. Pilot** | A few hundred MEO homes with the Connected Home kit and a MEO router | Two weeks of "what I would do", then live. Proves the learning and tunes trust and cost |
| **2. Launch** | Every MEO, then SFR, Connected Home customer (same Altice Labs platform) | An add-on inside the operator's app, with the free trial offered automatically |
| **3. Platform** | Other operators on Altice Labs' white-label platform | Alfred as a capability that Altice's own assistant can call |

**First customers:**
1. **Working households with heating:** regular schedules and cold arrivals; the clearest daily win.
2. **Homes on time-of-use energy tariffs:** heating planned around real arrivals.
3. **Older people living alone:** a missed morning routine becomes a gentle check-in for the family. This comes later, with care for consent.

**Revenue:** the subscription, plus services that situations lead to (a care visit, an energy tariff). These are proposed; no partner agreement exists.

## The subscription and the free trial
**Why one month free.** Alfred needs history before it can help, and a month is enough:
- **Week 1:** the first daily habits appear (5 occurrences each).
- **Weeks 2–4:** weekday and weekend times and each person's arrivals settle.
- **By the end of the month:** the strongest daily habits pass 0.75 and Alfred acts on them. On 28 simulated days, the evening light and bedtime were at 0.81 and 0.79. Rarer rhythms keep sharpening after that, with no extra setup.

One month is enough to feel it: the house is already warm when you get home.

**Conversion: the month sells it.** Before the trial ends, Alfred shows what it did for the household: how many times the house was warm when someone got home, the routines it handled, and how often people needed to step in. The choice to subscribe is made on a month of real comfort, not on a promise.

**How we know it works:**
- **Activation:** days to the first habit.
- **Value:** actions per week, and how warm the room is at each arrival.
- **Trust:** how often people undo or skip Alfred; it should fall week by week.
- **Conversion:** trial → paid within a week of the trial ending.
- **Cost:** tokens and € per home per day, logged from the pilot.

## The LLM: cheap by design
Alfred calls the LLM only when something happens in the home, about **15 times a day**; quiet moments need none. Each time it reads about 25k tokens and writes about 1k, as estimated on our prototype.

| Option | Per home per month | The home's data goes to | Best for |
|---|---|---|---|
| Frontier model, mid-tier | €16 | the model provider | the pilot; naming habits |
| **Small model** | **€0.80** | the model provider | routine decisions and chat at launch |
| Open model, EU-hosted | €2.30 | a provider in the EU | a data-residency requirement |
| Self-hosted at Altice Labs | €0.35–0.70 | nobody outside Altice Labs | thousands of homes; data sovereignty |
| Self-hosted at home | €30 (the box) | nobody: it never leaves the house | a premium, fully private tier |
| No LLM | €0 | nobody | outages, or homes that opt out of AI |

**Why self-host.**
- What Alfred sends the model is a diary of the home: who is in, when they sleep, when the house is empty.
- One GPU server at Altice Labs (€1,350 a month) serves about 4,000 homes and costs less than the small model from about 2,000 homes, but it needs a team to run it.
- A box at home also keeps Alfred working without internet.
- Open models are weaker than hosted ones. The guard's checks keep them safe, and the pilot has to prove their quality.

**Recommendation:**
1. **Pilot on a mid-tier frontier model** (€16 per home) to learn what good looks like.
2. **Launch on a small model** (€0.80, a fifth of the €4 price). Keep the frontier model for the nightly habit naming if quality needs it (under €1 more).
3. **Self-host at Altice Labs** from a few thousand homes: it costs less and the data stays in-house. Offer *at home* as a premium option.

