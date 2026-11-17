"""Simulator: the files it writes are in the kit's shape, ingest cleanly, and carry the planted habits.

  uv run python -m unittest discover -s tests -t . -v
"""
import json
import statistics
import tempfile
import unittest
import zipfile
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from source.diff import diff
from source.ingest import ingest
from source.normalize import normalize
from source.simulate import (PRESETS, REAL_HOME, TZ, Household, generate, home_a_max_days, household_from_dict,
                             houses, main)
from source.simulate_web import REAL_MAX_DAYS, _request, write_zip
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
