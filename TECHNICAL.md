# Alfred: technical design

> **In short.** Detection is statistics on the home's own history. There is no LLM in it and no hand-tuned threshold. Every decision asks one question: *does this beat chance in this home?* The LLM comes in afterwards, as an interpretation step that can only lower confidence. Code guards every change to a device.

The whole method has three fixed settings: a significance level $\alpha = 0.01$, at least **5 occurrences** for anything to count, and links at most **30 min** apart. Everything else is measured from the home itself, so a different home gets different habits from the same code.

## 1. Learning habits
| Step | What | How |
|---|---|---|
| **Rest** | Each signal's normal state: plug at 0 W, door closed | The value held longest; for numbers, the busiest band ± its wobble |
| **Happenings** | A signal leaving rest and coming back: *plug, 2,100 W, 3 min* | Start, end, size, duration. Alfred's own changes are left out, so it never learns itself |
| **Kinds** | Similar happenings: "the kettle" without anyone saying so | Gaussian mixture on (log size, log duration); BIC picks how many |
| **Links** | A then B on another device: "door, then kettle" | B follows A within 10–30 min more often than the home's daily rhythm explains (Poisson-binomial test) |
| **Patterns** | A kind, link or quiet stretch at a consistent time | The window test below |
| **Habits** | Patterns that recur together, with a rhythm and a person | Co-occurrence beyond chance; rhythm by exact tests |

**The window test.** For a candidate with $n$ occurrences, slide a 20, 40 or 60-minute window round the clock. A window holding $k$ of them is a pattern if

$$P\big(X \ge k\big) \cdot \text{tries} < \alpha, \qquad X \sim \text{Bin}(n,\ \pi_w)$$

- $\pi_w$ is the share of the home's active minutes that fall in the window. A busy hour needs more evidence than a quiet one.
- *tries* counts every candidate and window tested (Bonferroni).

**Rhythms.** Daily, then weekdays, every other weekend, every $n$ days or monthly. A finer rhythm is kept only if it beats its parent by an exact hypergeometric test. Weekday vs weekend times are split when Mann–Whitney says they differ. The habit's *person* is the one phone that arrives or leaves within it on at least 60% of its days.

**Confidence** of a habit:

$$c = \underbrace{\frac{k}{n}}_{\text{hit rate}} \times \underbrace{\frac{k}{k+1}}_{\text{evidence}} \times \underbrace{\Big(1 - \frac{\sigma}{\sigma_{\text{home}}}\Big)}_{\text{timing}}$$

One example can never give more than 0.5. Habits are re-learned every night from the whole history. A habit fades when its run of misses becomes unlikely under the rule of succession.

## 2. Spotting departures
- **Late.** With $n$ past start times, "late" means after the $\lceil(1-\alpha)(n+1)\rceil$-th earliest. This is a conformal bound, with no distribution assumed: in practice, "later than ever seen". A *missing habit* is 20 min past its window, not done, with someone home.
- **Occasions** (a party, a visit). Each stretch of the day is compared with the same stretch on usual days:
  - counts (door openings, guests' phones, plug uses) under a negative-binomial predictive;
  - durations (lights on, awake) by rank;
  - combined with Fisher's method.
  
  An *episode* needs **two** features beyond the home's history **and** a corrected $p < \alpha$. Episodes that look alike become one occasion, with confidence $\frac{n}{n+1} \times$ similarity. Live, a party is recognised from its first guests, and the occasion says how it moves tomorrow ("waking up +87 min").

| False alarm | How it's avoided |
|---|---|
| A holiday or outage reads as a missed habit | Only days the home was watched, with someone in, count |
| Many tests, one passes by luck | Every p-value is multiplied by the number of tries |
| One noisy sensor looks like an event | An occasion needs two unusual features at once |
| A party reads as a missed bedtime | An occasion *explains* the departures it causes; no alert for them |
| Acting on thin evidence | Below 0.4 nothing; below 0.75 only a suggestion |

## 3. Where the LLM comes in
The LLM is woken by what was learned, not by raw readings:

| Wake | When | Base confidence (code) |
|---|---|---|
| Habit soon | A habit is due within the warm-up time, and there is something to prepare | habit confidence × forecast probability × evidence |
| Arrival / departure | A household phone joins, or the last one leaves | 0.8 |
| Habit missing | As above | habit confidence × evidence |
| Occasion | Today matches a past occasion at ≥ 0.5 | the match's confidence |

- **It reads:** the habit's card, the 24 h forecast, who is home, the room and target temperature, the warm-up rate, and what is already planned.
- **It returns** proposals: *set the heating to 21 °C at 17:55, because Ana usually gets home at 18:40 and the house needs 25 min*, plus a **doubt** in [0, 1].
- **Code sets the final confidence:** $\text{confidence} = \text{base} \times (1 - \text{doubt})$. At ≥ 0.75 Alfred does it; below, it only suggests. The LLM can lower trust, never raise it.
- **Without any LLM**, scripted replies produce the same actions. The LLM adds judgement and a sentence people understand; detection never depends on it.

## 4. Safety
| Level | What | Who decides |
|---|---|---|
| Safe and reversible | Lamp, plug **off**, heating within limits | Alfred, at ≥ 0.75, logged |
| Adds load or cost | Plug **on** | Only a person |
| Never | Alarm, device resets, platform automations | Nobody: blocked in code |

- **Heating limits:** 16–23 °C, at most 3 °C per change and one change every 10 min; never above 18 °C with nobody home unless an arrival is due; hands off for 2 h after a person changes it.
- **Verification.** A change counts as done only if the device reports the new value afterwards.

## 5. Results on the demo house
60 simulated days of a two-person home: the data is simulated, the learning is real.

| What | Result |
|---|---|
| Waking up | Weekdays 07:08, weekends 08:53, confidence 0.90 |
| Ana gets home | Weekdays 18:38, her phone, 0.94. **Room at 21.2 °C when she arrives (18.3 °C before)** |
| João gets home | 19:28, but 20:54 on Tuesdays, 0.93 |
| Friday gathering | 4 seen, 6 guests' phones; recognised live at 20:25; next morning's warm-up moved +87 min |

## 6. Limits and next steps
- **A baseline comes first:** about a week for daily habits, several weeks for weekly ones.
- **The thermostat and the router's phone list are simulated today.** They need Altice's platform to expose them.
- **Next:** the smoke rule as code (red light, plug off, no LLM); appliance wear ("the kettle is getting slower"); an insulation score from the measured heat loss.
