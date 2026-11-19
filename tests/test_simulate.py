"""Simulator: the files it writes are in the kit's shape, ingest cleanly, and carry the planted habits.

  uv run python -m unittest discover -s tests -t . -v
"""
import hashlib
import json
import statistics
import tempfile
import unittest
import zipfile
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from source.diff import diff
from source.ingest import ingest
from source.normalize import normalize
from source.simulate import (PRESETS, REAL_HOME, TZ, Household, generate, home_a_max_days, household_from_dict,
                             houses, main)
from source.simulate_web import REAL_MAX_DAYS, _request, day_view, write_zip
from source.store import Store

LIVE = json.loads((Path(__file__).parent / "fixtures" / "get_home_live.json").read_text())
START = date(2026, 9, 1)
STALE_MS = 60 * 60_000


def _snaps(household, days=14, **kw) -> list[tuple[int, dict]]:
    lines, _ = generate(household, days, start=START, **kw)
    return [(row["ts"], row["home"]) for row in map(json.loads, lines)]


def _events(snaps) -> list:
    out, prev, prev_ts = [], None, None
    for ts, home in snaps:
        state = normalize(home)
        if prev is not None:
            out += diff(prev, state, ts, STALE_MS, prev_ts)
        prev, prev_ts = state, ts
    return out


def _bursts(events) -> list[tuple[datetime, float, int]]:
    """Plug uses as (local start, seconds, peak watts): from leaving rest to returning to it."""
    out, start, peak, rest = [], None, 0, None
    for e in events:
        if e.capability != "instantPower":
            continue
        rest = e.old if rest is None else rest
        if start is None and e.new > rest + 20:
            start, peak = e.ts, e.new
        elif start is not None and e.new <= rest + 20:
            out.append((datetime.fromtimestamp(start / 1000, TZ), (e.ts - start) / 1000, peak))
            start = None
        elif start is not None:
            peak = max(peak, e.new)
    return out


class ShapeTest(unittest.TestCase):
    def test_same_fields_as_the_real_kit(self):
        def shape(home):
            return {(d["type"], d["id"]): sorted(c["type"] for c in d["capabilities"]) for d in home["devices"]}

        for name in (*PRESETS, REAL_HOME):
            for _, home in _snaps(name, days=2):
                self.assertEqual(shape(home), shape(LIVE), name)
                self.assertEqual(set(home), set(LIVE))

    def test_uses_the_kit_values(self):
        seen: dict[str, set] = {}
        for e in _events(_snaps("rosa")):
            seen.setdefault(f"{e.device}.{e.capability}", set()).update((e.old, e.new))
        self.assertEqual(seen["Contact sensor.sensorState"], {"normal", "alarm"})
        self.assertEqual(seen["Light.power"], {"on", "off"})

    def test_covers_whole_local_days_in_order_without_gaps(self):
        snaps = _snaps("rosa", days=3)
        times = [ts for ts, _ in snaps]
        self.assertEqual(datetime.fromtimestamp(times[0] / 1000, TZ), datetime(2026, 9, 1, tzinfo=TZ))
        self.assertLess(datetime.fromtimestamp(times[-1] / 1000, TZ), datetime(2026, 9, 4, tzinfo=TZ))
        self.assertEqual(times, sorted(set(times)))
        self.assertLessEqual(max(b - a for a, b in zip(times, times[1:])), 300_000)

    def test_no_device_goes_stale(self):
        for name in ("students", REAL_HOME):
            self.assertFalse([e for e in _events(_snaps(name, days=5)) if e.kind != "change"], name)

    def test_same_seed_same_file(self):
        first = list(generate("rosa", 3, seed=7)[0])
        self.assertEqual(first, list(generate("rosa", 3, seed=7)[0]))
        self.assertNotEqual(first, list(generate("rosa", 3, seed=8)[0]))

    def test_plug_reports_current_and_daily_energy(self):
        snaps = _snaps("rosa", days=2)

        def plug(home):
            return {c["type"]: c["value"] for d in home["devices"] if d["type"] == "plug" for c in d["capabilities"]}

        on = next(plug(h) for _, h in snaps if plug(h)["instantPower"] > 1000)
        self.assertAlmostEqual(on["instantCurrent"], on["instantPower"] / 230 * 1000, delta=1)  # mA
        day1 = plug([h for ts, h in snaps if datetime.fromtimestamp(ts / 1000, TZ).day == 1][-1])
        day2 = plug(snaps[-1][1])
        self.assertGreater(day1["todayConsumption"], 0.05)  # a kettle: about 0.1 kWh
        self.assertEqual(day2["yesterdayConsumption"], day1["todayConsumption"])


class HabitsTest(unittest.TestCase):
    def test_rosa_has_a_morning_kettle_most_days_at_a_consistent_time(self):
        bursts = _bursts(_events(_snaps("rosa")))
        self.assertGreaterEqual(len(bursts), 9)  # some days are skipped
        self.assertLess(len(bursts), 14)
        weekdays = [b[0].hour * 60 + b[0].minute for b in bursts if b[0].weekday() < 5]
        self.assertLess(statistics.pstdev(weekdays), 30)  # the plan's bar for a habit
        self.assertTrue(all(435 <= m <= 545 for m in weekdays), weekdays)  # around 08:05, give or take
        for _, seconds, watts in bursts:
            self.assertTrue(120 <= seconds <= 260 and 1950 <= watts <= 2250, (seconds, watts))

    def test_the_night_is_quiet(self):
        for e in _events(_snaps("rosa")):
            hour = datetime.fromtimestamp(e.ts / 1000, TZ).hour
            if 1 <= hour < 6:
                self.assertIn(e.capability, ("temperatureValue", "humidityValue"), e.describe())

    def test_a_different_household_gives_different_patterns(self):
        rosa, couple = _bursts(_events(_snaps("rosa"))), _bursts(_events(_snaps("couple")))
        self.assertLess(statistics.mean(b[2] for b in couple), 1400)
        self.assertGreater(statistics.mean(b[1] for b in couple), 1.5 * statistics.mean(b[1] for b in rosa))
        doors = lambda name: sum(e.new == "alarm" for e in _events(_snaps(name)) if e.capability == "sensorState")
        self.assertGreater(doors("students"), 1.5 * doors("rosa"))

    def test_the_answer_key_matches_the_data(self):
        lines, truth = generate("rosa", 14, start=START)
        planted = [t["start"] for t in truth if t["what"] == "morning use"]
        found = [b[0].strftime("%Y-%m-%dT%H:%M:%S") for b in _bursts(_events(
            [(r["ts"], r["home"]) for r in map(json.loads, lines)]))]
        self.assertEqual(planted, found)

    def test_idle_power_is_the_plugs_rest(self):
        values = [e.new for e in _events(_snaps("students", days=3)) if e.capability == "instantPower"]
        self.assertGreater(sum(7 <= v <= 9 for v in values), len(values) / 2)


class RealHomeTest(unittest.TestCase):
    def test_replays_the_recorded_coffee_maker(self):
        bursts = [b for b in _bursts(_events(_snaps(REAL_HOME))) if b[1] > 300]
        self.assertGreaterEqual(len(bursts), 10)
        self.assertTrue(all(900 <= b[2] <= 1100 for b in bursts), bursts)
        self.assertGreaterEqual(sum(b[0].strftime("%H:%M") in ("06:29", "06:30") for b in bursts), 8)
        self.assertFalse([b for b in bursts if b[0].weekday() == 6])  # never on a Sunday

    def test_unmetered_days_replay_a_recorded_use(self):
        _, truth = generate(REAL_HOME, 40, start=START)
        sources = {t.get("source") for t in truth if t["what"] == "coffee maker"}
        self.assertEqual(sources, {"recorded", "recorded use, replayed on an unmetered day"})

    def test_refuses_more_days_than_were_recorded(self):
        limit = home_a_max_days(START)
        list(generate(REAL_HOME, limit, start=START)[0])
        with self.assertRaisesRegex(ValueError, "recorded days"):
            generate(REAL_HOME, limit + 1, start=START)


class ConfigTest(unittest.TestCase):
    def test_reads_loose_values(self):
        h = household_from_dict({"wake": "06:00", "appliance_watts": "900", "outing_days": "Mon, Wednesday"})
        self.assertEqual((h.wake, h.appliance_watts, h.outing_days), ("06:00", 900.0, "Mon, Wednesday"))

    def test_rejects_bad_values(self):
        for bad in ({"wake": "25:00"}, {"nope": 1}, {"away_chance": 2}, {"appliance_watts": "lots"},
                    {"outing_days": "someday"}, {"appliance_minutes": 0}):
            with self.assertRaises(ValueError, msg=bad):
                household_from_dict(bad)

    def test_bedtime_after_midnight(self):
        _, truth = generate(Household(wake="10:00", bed="01:30", away_chance=0), 2, start=START)
        beds = [t["start"] for t in truth if t["what"] == "bed"]
        self.assertTrue(beds[0].startswith("2026-09-02T0"), beds)

    def test_rejects_an_interval_that_ingest_would_call_a_gap(self):
        with self.assertRaises(ValueError):
            generate("rosa", 1, interval_s=3600)

    def test_command_writes_the_file_and_the_answer_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, truth = Path(tmp) / "sim" / "rosa.jsonl", Path(tmp) / "truth.json"
            self.assertEqual(main(["--days", "2", "--out", str(out), "--truth", str(truth)]), 0)
            self.assertEqual(len(out.read_text().splitlines()), len(_snaps("rosa", days=2)))
            self.assertTrue(json.loads(truth.read_text()))
            self.assertEqual(main(["--days", "2", "--household", "nobody", "--out", str(out)]), 1)


def _local_ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp() * 1000)


ACTIVITY = ("sensorState", "power", "instantPower")
CLIMATE = ("temperatureValue", "humidityValue")


def _changes(lines, caps) -> list[tuple]:
    snaps = [(row["ts"], row["home"]) for row in map(json.loads, lines)]
    return [(e.ts, e.device, e.capability, e.new) for e in _events(snaps) if e.capability in caps]


def _rosa(days=14, **options):
    return generate(household_from_dict(asdict(PRESETS["rosa"]) | options), days, start=START)


class TestOptionsTest(unittest.TestCase):
    """holidays, outages and changes: each changes only what it should."""

    def setUp(self):
        lines, self.truth = _rosa()
        self.lines = list(lines)

    def test_a_holiday_is_quiet_and_leaves_the_other_days_alone(self):
        lines, truth = _rosa(holidays=["2026-09-05..2026-09-07"])
        lines = list(lines)
        first, end = _local_ms("2026-09-05T00:00"), _local_ms("2026-09-08T06:00")  # with that night's bedtime
        outside = lambda changes: [c for c in changes if not first <= c[0] < end]
        self.assertEqual(outside(_changes(lines, ACTIVITY)), outside(_changes(self.lines, ACTIVITY)))
        self.assertFalse([c for c in _changes(lines, ACTIVITY) if first <= c[0] < _local_ms("2026-09-08T00:00")])
        self.assertEqual(_changes(lines, CLIMATE), _changes(self.lines, CLIMATE))  # nobody home; the room carries on
        times = [json.loads(line)["ts"] for line in lines]
        self.assertLessEqual(max(b - a for a, b in zip(times, times[1:])), 300_000)  # snapshots continue
        self.assertEqual([t["start"][:10] for t in truth if t["what"] == "away (holiday)"],
                         ["2026-09-05", "2026-09-06", "2026-09-07"])
        on_holiday = [t for t in truth if "2026-09-05" <= t["start"][:10] <= "2026-09-07"]
        self.assertEqual({t["what"] for t in on_holiday}, {"away (holiday)"})

    def test_an_outage_has_no_snapshots_and_ingest_sees_a_gap(self):
        a, b = _local_ms("2026-09-10T22:00"), _local_ms("2026-09-11T08:00")
        lines, truth = _rosa(outages=["2026-09-10T22:00..2026-09-11T08:00"])
        lines = list(lines)
        times = [json.loads(line)["ts"] for line in lines]
        self.assertFalse([ts for ts in times if a <= ts < b])
        self.assertEqual(lines, [line for line in self.lines if not a <= json.loads(line)["ts"] < b])
        snaps = [(row["ts"], row["home"]) for row in map(json.loads, lines)]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp), record=False)
            ingest(snaps, store, stale_ms=STALE_MS)
            gaps = [(e.old, e.new) for e in store.events_since(-1) if e.kind == "gap"]
            store.close()
        self.assertEqual(len(gaps), 1)
        self.assertTrue(gaps[0][0] < a and gaps[0][1] >= b, gaps)
        self.assertIn({"what": "outage", "start": "2026-09-10T22:00:00", "end": "2026-09-11T08:00:00"}, truth)
        self.assertEqual([t for t in truth if t["what"] != "outage"], self.truth)

    def test_a_change_moves_the_habits_from_that_day(self):
        lines, truth = _rosa(changes=[{"from": "2026-09-08", "wake": "10:00"}])
        lines = list(lines)
        cut = _local_ms("2026-09-08T00:00")
        before = lambda changes: [c for c in changes if c[0] < cut]
        self.assertEqual(before(_changes(lines, ACTIVITY)), before(_changes(self.lines, ACTIVITY)))
        self.assertEqual([t for t in truth if t["start"] < "2026-09-08" and t["what"] != "change"],
                         [t for t in self.truth if t["start"] < "2026-09-08"])
        wakes = [datetime.fromisoformat(t["start"]) for t in truth if t["what"] == "wake" and t["start"] >= "2026-09-08"]
        self.assertTrue(wakes and all(9 <= w.hour <= 11 for w in wakes), wakes)
        self.assertIn({"what": "change", "start": "2026-09-08T00:00:00", "settings": {"wake": "10:00"}}, truth)

    def test_the_same_seed_gives_the_same_output(self):
        options = {"holidays": ["2026-09-03"], "outages": ["2026-09-02T01:00..2026-09-02T03:00"],
                   "changes": [{"from": "2026-09-04", "bed": "22:00"}]}
        first = [list(lines) + [json.dumps(truth)] for lines, truth in [_rosa(5, **options)]]
        self.assertEqual(first, [list(lines) + [json.dumps(truth)] for lines, truth in [_rosa(5, **options)]])

    def test_defaults_add_nothing(self):
        self.assertEqual(Household().holidays + Household().outages + Household().changes, [])
        self.assertEqual(self.lines, list(generate("rosa", 14, start=START)[0]))

    def test_rejects_bad_options(self):
        for bad in ({"holidays": ["2026-09-31"]}, {"holidays": ["2026-09-07..2026-09-05"]},
                    {"outages": ["2026-09-10T22:00"]}, {"outages": ["2026-09-11T08:00..2026-09-10T22:00"]},
                    {"changes": [{"wake": "09:00"}]}, {"changes": [{"from": "2026-10-01"}]},
                    {"changes": [{"from": "2026-10-01", "name": "x"}]}, {"changes": [{"from": "2026-10-01", "wake": "9h"}]},
                    {"changes": "wake later"}):
            with self.assertRaises(ValueError, msg=bad):
                household_from_dict(bad)

    def test_config_file_carries_the_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, out, truth = Path(tmp) / "c.json", Path(tmp) / "r.jsonl", Path(tmp) / "t.json"
            config.write_text(json.dumps({"holidays": ["2026-09-02"]}))
            self.assertEqual(main(["--days", "3", "--config", str(config), "--out", str(out), "--truth", str(truth)]), 0)
            self.assertIn("away (holiday)", {t["what"] for t in json.loads(truth.read_text())})


# 14 days of each preset (lines, then the answer key), from before the rarer rhythms were added: options that are
# off by default must not move a byte. A preset changed on purpose needs its digest updated here.
PRESET_DIGESTS = {
    "rosa": "7fd757432c0646b45bdb8dae0330d7ebb6f57d03ed921ba169e3606b4710f1e9",
    "couple": "855cf115c89d37e6a1c9544ac8953d164b0dc53f0feac3b2d751ae88ac36d209",
    "students": "35025cb44a90a5e699f0f67c20459d7a0e4f6f6e56b4d87c9773ca87e3071b9d",
}
ANCHOR = date(2026, 9, 5)  # rosa-plus's first visit, a Saturday
HOURLY = (11 * 60, 14 * 60, 17 * 60, 20 * 60)  # rosa-plus's tea, every 3 h from 11:00 to 20:00


def _digest(household, days: int = 14) -> str:
    lines, truth = generate(household, days, start=START)
    out = hashlib.sha256()
    for line in lines:
        out.update(line.encode())
    out.update(json.dumps(truth).encode())
    return out.hexdigest()


def _day(t: dict) -> date:
    return datetime.fromisoformat(t["start"]).date()


def _clock(t: dict) -> int:
    at = datetime.fromisoformat(t["start"])
    return at.hour * 60 + at.minute


def _first_sunday(d: date) -> bool:
    return d.weekday() == 6 and d.day <= 7


class RarerRhythmsTest(unittest.TestCase):
    """rosa-plus: a visit every other Saturday, a monthly outing, tea every 3 h and parties, all in the answer key.
    A year of it, answer key only (the snapshots are never built)."""

    @classmethod
    def setUpClass(cls):
        _, cls.truth = generate("rosa-plus", 365, start=START)
        cls.parties = [t for t in cls.truth if t["what"] == "party"]

    def planted(self, label: str) -> list[dict]:
        return [t for t in self.truth if t["what"].startswith(label)]

    def test_the_other_presets_are_unchanged(self):
        for name, digest in PRESET_DIGESTS.items():
            self.assertEqual(_digest(name), digest, name)
        for h in (*(PRESETS[name] for name in PRESET_DIGESTS), *houses(20)):
            self.assertEqual((h.visit_every_days, h.monthly_on, h.hourly_every_h, h.parties_per_30_days),
                             (0, "", 0, 0), h.name)

    def test_visits_every_other_saturday_now_and_then_skipped(self):
        visits = self.planted("visit (")
        scheduled = [d for d in (START + timedelta(days=i) for i in range(365)) if (d - ANCHOR).days % 14 == 0]
        self.assertEqual({t["what"] for t in visits}, {"visit (every other Saturday)"})
        self.assertTrue(set(map(_day, visits)) < set(scheduled))  # only those days, and not every one of them
        self.assertGreater(len(visits), 0.75 * len(scheduled))
        arrivals = [_clock(t) for t in visits]
        self.assertLess(abs(statistics.mean(arrivals) - 15 * 60), 15)
        self.assertGreater(statistics.pstdev(arrivals), 5)
        self.assertEqual(visits[0]["recurrence"], {
            "kind": "every_n_days", "weekdays": ["sat"], "every_n_days": 14, "anchor": "2026-09-05",
            "day_of_month": None, "every_n_hours": None, "text": "every other Saturday"})
        teas = self.planted("visit use")  # the kettle, during the visit
        self.assertGreater(len(teas), 0.8 * len(visits))
        self.assertTrue(all(any(v["start"] < t["start"] < v["end"] for v in visits) for t in teas))

    def test_a_monthly_outing_on_the_first_sunday(self):
        outings = self.planted("monthly outing")
        firsts = [d for d in (START + timedelta(days=i) for i in range(365)) if _first_sunday(d)]
        self.assertEqual({t["what"] for t in outings}, {"monthly outing (first Sunday)"})
        self.assertTrue(set(map(_day, outings)) <= set(firsts))
        self.assertGreaterEqual(len(outings), len(firsts) - 3)
        self.assertEqual(outings[0]["recurrence"], {
            "kind": "monthly", "weekdays": ["sun"], "every_n_days": None, "anchor": outings[0]["start"][:10],
            "day_of_month": None, "every_n_hours": None, "text": "on the first Sunday of the month"})
        uses = [t for t in self.truth if t.get("appliance")]
        for out in outings:  # nobody home: the kettle never runs
            self.assertFalse([u for u in uses if out["start"] <= u["start"] <= out["end"]], out)

    def test_tea_every_3_hours_from_11_to_20(self):
        teas = self.planted("every 3 h")
        self.assertEqual({t["what"] for t in teas}, {"every 3 h, 11:00 to 20:00"})
        offsets = [min((_clock(t) - slot for slot in HOURLY), key=abs) for t in teas]
        self.assertTrue(all(abs(m) <= 45 for m in offsets), offsets)
        self.assertGreater(statistics.pstdev(offsets), 5)  # about the same times, not the exact minute
        self.assertLessEqual(max(Counter(map(_day, teas)).values()), len(HOURLY))
        self.assertTrue(0.6 * 365 * len(HOURLY) < len(teas) < 0.95 * 365 * len(HOURLY))  # some times are skipped
        self.assertEqual(teas[0]["recurrence"], {
            "kind": "hourly", "weekdays": None, "every_n_days": None, "anchor": None, "day_of_month": None,
            "every_n_hours": 3, "text": "every 3 hours from 11:00 to 20:00"})
        runs = sorted((t["start"], t["end"]) for t in self.truth if t.get("appliance"))
        self.assertTrue(all(a[1] < b[0] for a, b in zip(runs, runs[1:])))  # no two uses of the plug overlap

    def test_three_or_four_parties_a_month_mostly_on_fridays_and_saturdays(self):
        days = [_day(t) for t in self.parties]
        self.assertTrue(3 <= len(days) / (365 / 30) <= 4, len(days))
        self.assertGreater(sum(d.weekday() in (4, 5) for d in days), 0.75 * len(days))
        self.assertTrue(all((b - a).days >= 2 for a, b in zip(days, days[1:])))
        self.assertFalse([d for d in days if (d - ANCHOR).days % 14 == 0 or _first_sunday(d)])  # one thing a day
        self.assertTrue(all(t["door_openings"] >= 6 for t in self.parties))
        self.assertGreater(sum(t["plug_uses"] for t in self.parties), len(self.parties))
        uses = self.planted("party use")
        self.assertTrue(all(any(p["start"] < u["start"] < p["end"] for p in self.parties) for u in uses))

    def test_a_party_means_a_late_night_and_a_late_morning(self):
        beds = {_day(t) - timedelta(days=_clock(t) < 12 * 60): t for t in self.truth if t["what"] == "bed"}
        wakes = {_day(t): t for t in self.truth if t["what"] == "wake"}
        usual = statistics.median(_clock(t) for d, t in wakes.items() if d.weekday() >= 5 and "after" not in t)
        parties = [p for p in self.parties if _day(p) < START + timedelta(days=364)]  # bed after the year ends
        late = []
        for party in parties:
            night, bed = _day(party), beds[_day(party)]
            self.assertEqual(bed.get("after"), "party")
            self.assertGreater(bed["start"], party["end"])
            morning = wakes.get(night + timedelta(days=1))
            if morning:  # unless away all day
                self.assertEqual(morning.get("after"), "party")
                if _day(morning).weekday() >= 5:
                    late.append(_clock(morning) - usual)
        self.assertGreater(sum(_clock(beds[_day(p)]) < 12 * 60 for p in parties), 0.8 * len(parties))  # after midnight
        self.assertGreater(statistics.mean(late), 50)  # about 90 min later than a usual weekend morning
        self.assertEqual(sum(t.get("after") == "party" for t in self.truth if t["what"] == "bed"), len(parties))

    def test_a_party_shows_in_the_snapshots(self):
        lines, truth = generate("rosa-plus", 14, start=START)
        party = next(t for t in truth if t["what"] == "party")
        a, b = _local_ms(party["start"]), _local_ms(party["end"])
        events = _events([(r["ts"], r["home"]) for r in map(json.loads, lines)])
        opened = [e.ts for e in events if e.capability == "sensorState" and e.new == "alarm" and a <= e.ts <= b]
        self.assertGreaterEqual(len(opened), party["door_openings"] - 1)  # an everyday opening may join one of theirs
        self.assertGreaterEqual(sum(ts < a + 45 * 60_000 for ts in opened), 4)  # the guests arrive within minutes
        light = [(e.ts, e.new) for e in events if e.capability == "power" and e.device == "Light"]
        self.assertEqual([on for ts, on in light if ts <= b][-1], "on")  # on as the last guests leave
        off = next(ts for ts, on in light if ts > b)  # and off at the late bedtime, a little after
        self.assertEqual(off, _local_ms(next(t["start"] for t in truth if t["what"] == "bed" and t.get("after"))))
        self.assertGreaterEqual(off - b, 15 * 60_000)
        bursts = {s[0].strftime("%Y-%m-%dT%H:%M:%S") for s in _bursts(events)}
        teas = {t["start"] for t in truth if t["what"] == "party use" and party["start"] < t["start"] < party["end"]}
        self.assertEqual(len(teas), party["plug_uses"])
        self.assertLessEqual(teas, bursts)
        morning = next(t for t in truth if t["what"] == "wake" and t.get("after") == "party")
        self.assertIn((_local_ms(morning["start"]), "on"), light)  # the late morning starts with the lamp

    def test_a_shorter_run_is_the_start_of_a_longer_one(self):
        night = _day(self.parties[0])
        for days in ((night - START).days + 1, (night - START).days + 2):  # cut in the party's night, then its morning
            short, short_truth = generate("rosa-plus", days, start=START)
            long, long_truth = generate("rosa-plus", days + 1, start=START)
            cut = _local_ms((START + timedelta(days=days)).isoformat() + "T00:00")
            self.assertEqual(list(short), [line for line in long if json.loads(line)["ts"] < cut], days)
            self.assertEqual(short_truth, [t for t in long_truth if _local_ms(t["start"]) < cut], days)

    def test_a_holiday_cancels_the_visit_or_the_party(self):
        visit_day = _day(self.planted("visit (")[1])
        _, truth = generate(household_from_dict(asdict(PRESETS["rosa-plus"]) | {"holidays": [visit_day.isoformat()]}),
                            60, start=START)
        _, base = generate("rosa-plus", 60, start=START)
        first, end = visit_day.isoformat(), f"{visit_day + timedelta(days=1)}T06:00"  # with that night's bedtime
        outside = lambda entries: [t for t in entries if not first <= t["start"] < end]
        self.assertEqual(outside(truth), outside(base))  # no party falls on a visit's day, so no lie-in moves
        self.assertEqual([t["what"] for t in truth if _day(t) == visit_day], ["away (holiday)"])
        night = _day(self.parties[0])
        _, truth = generate(household_from_dict(asdict(PRESETS["rosa-plus"]) | {"holidays": [night.isoformat()]}),
                            60, start=START)
        self.assertFalse([t for t in truth if t["what"] == "party" and _day(t) == night])
        self.assertFalse([t for t in truth if t.get("after") and _day(t) <= night + timedelta(days=1)])
        self.assertEqual([t for t in truth if _day(t) < night], [t for t in base if _day(t) < night])

    def test_a_change_can_stop_the_parties(self):
        h = household_from_dict(asdict(PRESETS["rosa-plus"]) | {"changes": [{"from": "2026-10-01", "parties_per_30_days": 0}]})
        _, truth = generate(h, 90, start=START)
        parties = [t["start"] for t in truth if t["what"] == "party"]
        self.assertTrue(parties and all(p < "2026-10-01" for p in parties), parties)

    def test_monthly_on_a_day_of_the_month_or_the_last_friday(self):
        for spec, label, text, on_day in (
                ("15", "monthly outing (the 15th)", "on the 15th of the month", lambda d: d.day == 15),
                ("last fri", "monthly outing (last Friday)", "on the last Friday of the month",
                 lambda d: d.weekday() == 4 and (d + timedelta(days=7)).month != d.month)):
            h = household_from_dict({"monthly_on": spec, "monthly_chance": 1, "away_chance": 0, "outing_days": ""})
            _, truth = generate(h, 120, start=START)
            outings = [t for t in truth if t["what"].startswith("monthly")]
            self.assertEqual({t["what"] for t in outings}, {label})
            self.assertEqual({t["recurrence"]["text"] for t in outings}, {text})
            self.assertEqual(len(outings), 4, spec)  # September to December
            self.assertTrue(all(on_day(_day(t)) for t in outings), [t["start"] for t in outings])

    def test_reads_and_checks_the_new_settings(self):
        h = household_from_dict({"visit_every_days": "14", "visit_anchor": "2026-09-05", "monthly_on": " Last Fri ",
                                 "hourly_every_h": "2.5", "parties_per_30_days": "3", "party_days": "Fri, Saturday"})
        self.assertEqual((h.visit_every_days, h.monthly_on, h.hourly_every_h, h.party_days),
                         (14, "Last Fri", 2.5, "Fri, Saturday"))
        for bad in ({"visit_every_days": 14}, {"visit_every_days": 14, "visit_anchor": "soon"},
                    {"visit_every_days": "14.5", "visit_anchor": "2026-09-05"}, {"visit_every_days": -14},
                    {"monthly_on": "every sun"}, {"monthly_on": "32"}, {"monthly_on": "fifth sun"},
                    {"hourly_every_h": 0.1}, {"hourly_every_h": 3, "hourly_from": "20:00", "hourly_until": "08:00"},
                    {"hourly_from": "8h"}, {"parties_per_30_days": -1}, {"parties_per_30_days": 20},
                    {"party_days": "someday"}, {"visit_chance": 2}, {"party_minutes": 30}):
            with self.assertRaises(ValueError, msg=bad):
                household_from_dict(bad)
        for name, preset in PRESETS.items():
            self.assertEqual(household_from_dict(asdict(preset)), preset, name)  # every preset is a valid config

    def test_the_3d_view_serves_it(self):
        view = day_view("rosa-plus", (ANCHOR - START).days + 1)
        self.assertIn("visit (every other Saturday)", {t["what"] for t in view["truth"]})


class HousesTest(unittest.TestCase):
    def test_houses_differ_and_repeat(self):
        made = houses(6)
        self.assertEqual([h.name for h in made], [f"house-{n}" for n in range(1, 7)])
        self.assertEqual(made, houses(6))
        self.assertGreater(len({(h.wake, h.appliance, h.appliance_watts) for h in made}), 4)
        self.assertNotEqual(made, houses(6, seed=2))

    def test_every_made_up_house_is_a_valid_household_that_generates(self):
        for h in houses(25):
            household_from_dict(asdict(h))
            self.assertFalse([e for e in _events(_snaps(h, days=2)) if e.kind != "change"], h.name)

    def test_command_writes_a_folder_of_houses(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["--days", "1", "--houses", "2", "--out", tmp]), 0)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             ["house-1.jsonl", "house-2.jsonl"])


class FormTest(unittest.TestCase):
    def test_reads_simulated_houses(self):
        self.assertEqual(_request({"days": "14", "houses": "3"}), ("simulated", 14, 3))
        self.assertEqual(_request({"kind": "simulated", "days": "14", "houses": "3"}), ("simulated", 14, 3))

    def test_reads_the_real_house(self):
        self.assertEqual(_request({"kind": "real", "days": "30", "houses": "7"}), ("real", 30, 1))
        self.assertEqual(_request({"kind": "real", "days": str(REAL_MAX_DAYS)}), ("real", REAL_MAX_DAYS, 1))

    def test_bad_input_is_a_plain_error(self):
        for bad in ({"days": "many", "houses": "1"}, {"days": "0", "houses": "1"}, {"days": "3"},
                    {"days": "3", "houses": "99"}, {"days": "365", "houses": "20"},
                    {"kind": "real", "days": str(REAL_MAX_DAYS + 1)}, {"kind": "other", "days": "3", "houses": "1"}):
            with self.assertRaises(ValueError, msg=bad):
                _request(bad)

    def test_zip_has_one_file_per_simulated_house(self):
        with tempfile.TemporaryFile() as tmp:
            write_zip(tmp, "simulated", 2, 2)
            with zipfile.ZipFile(tmp) as z:
                self.assertEqual(sorted(z.namelist()), ["house-1.jsonl", "house-2.jsonl"])
                self.assertEqual(z.read("house-2.jsonl").decode(), "".join(generate(houses(2)[1], 2)[0]))

    def test_zip_of_the_real_house(self):
        with tempfile.TemporaryFile() as tmp:
            write_zip(tmp, "real", 2)
            with zipfile.ZipFile(tmp) as z:
                self.assertEqual(z.namelist(), ["home-a.jsonl"])
                self.assertEqual(z.read("home-a.jsonl").decode(), "".join(generate(REAL_HOME, 2)[0]))


if __name__ == "__main__":
    unittest.main()
