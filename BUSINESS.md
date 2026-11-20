# Alfred: business model

> **In short.** Alfred is sold through Altice as an add-on to the Connected Home offer: no new hardware, no new app. Families get **one month free** while Alfred learns them, then about **€4 a month**. When the trial ends Alfred keeps learning but stops acting on its own, so people notice what was automatic. Because the LLM is called only on learned events, it costs **cents to a few dollars per home per month**.

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

**Conversion: you notice it when it stops.** After the trial, Alfred doesn't disappear. It drops from *doing* to *suggesting*: "I would have warmed the house at 17:55." What was automatic becomes manual again, and that is the reason to pay.

| | Free, after the trial | Paid |
|---|---|---|
| Learns habits and occasions | ✓ | ✓ |
| Shows the plan and suggests | ✓ | ✓ |
| Acts ahead of habits (heating, light) | – | ✓ |
| Chat and voice | limited | ✓ |

**How we know it works:**
- **Activation:** days to the first habit.
- **Value:** actions per week, and how warm the room is at each arrival.
- **Trust:** how often people undo or skip Alfred; it should fall week by week.
- **Conversion:** trial → paid within a week of the trial ending.
- **Cost:** LLM calls per home per day.

## The LLM: cheap by design
Each LLM call reads about 12k tokens and writes about 1k, as measured on our prototype. What matters is **how often** it is called.

| Calls per home per day | Frontier model | Small model | Open model, EU-hosted |
|---|---|---|---|
| ~110 (on every 15-min check) | ≈ $104 / month | ≈ $35 | ≈ $10 |
| ~10 (on every learned event) | ≈ $9.5 | ≈ $3.2 | ≈ $0.9 |
| **~3 (only when something is ambiguous)** | **≈ $2.8** | **≈ $0.95** | **≈ $0.3** |

| Option | Privacy | Quality | Fits when |
|---|---|---|---|
| Frontier hosted | Data goes to the provider | Best judgement and language | The pilot; chat |
| Small hosted | Same | Good for structured proposals | Routine decisions |
| Open model, EU-hosted | Stays in the EU | Enough with the guard's checks | Launch at scale |
| Self-hosted at Altice | Never leaves Altice | As open models | Thousands of homes, or a data-sovereignty requirement |
| No LLM | Best | Same actions, fixed sentences, no chat | The free tier; outages |

**Recommendation:**
1. Call the LLM **only when something is ambiguous**: chat, a party, a missing routine. Routine actions that code is already sure of need no LLM.
2. **Pilot on a frontier model** to learn what good looks like.
3. **Launch on a small or EU-hosted open model**, keeping the LLM under 15% of the price.
4. **Move to self-hosting** when volume or data sovereignty justifies the GPUs.

*Assumptions to validate: the €4 price, in a €3–5 range (SFR's full security service is €14.99); the trial length; token counts per call; list prices of $3/$15, $1/$5 and $0.2/$0.6 per million input/output tokens, with the fixed prompt cached.*
