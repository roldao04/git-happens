"""Ingest: JSONL snapshots → events in the store, at full speed, with every time from the data.

  uv run python -m unittest discover -s tests -t . -v
"""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from source.agent import StubAgent
from source.clock import TZ, day_bounds
from source.ingest import IngestStats, ingest, read_jsonl
from source.loop import run
from source.model import Event
from source.normalize import normalize
from source.sources import ReplaySource
from source.store import Store
from source.triggers import Triggers, seed_triggers

MORNING = Path(__file__).parent / "fixtures" / "morning.jsonl"
STALE_MS = 60 * 60_000


def _events(store: Store) -> list[tuple]:
    return [(e.ts, e.device_id, e.capability, e.kind, e.old, e.new) for e in store.events_since(-1)]


def _count(store: Store, table: str) -> int:
    return store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class ReadJsonlTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "snaps.jsonl"

    def test_reads_lazily(self):
        self.path.write_text('{"ts": 1, "home": {"devices": []}}\n{not json\n')
        snaps = read_jsonl(self.path)
        self.assertEqual(next(snaps), (1, {"devices": []}))
        with self.assertRaises(json.JSONDecodeError):
            next(snaps)

    def test_skips_blank_lines(self):
        self.path.write_text('\n{"ts": 1, "home": {}}\n\n{"ts": 2, "home": {}}\n')
        self.assertEqual([ts for ts, _ in read_jsonl(self.path)], [1, 2])


class IngestTest(unittest.TestCase):
    def _store(self) -> Store:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name), record=False)
        self.addCleanup(store.close)
        return store

    def test_same_events_as_loop(self):
        via_loop = self._store()
        with self.assertLogs("source", "INFO"):  # keep the loop's dry-run rule lines off the test output
            asyncio.run(run(ReplaySource(MORNING), via_loop, Triggers(seed_triggers(15)), StubAgent(),
                            poll_seconds=60, stale_minutes=60))
        via_ingest = self._store()
        ingest(read_jsonl(MORNING), via_ingest, stale_ms=STALE_MS)
        self.assertTrue(_events(via_loop))
        not_initial = [e for e in _events(via_ingest) if e[3] != "initial"]  # only ingest records starting values
        self.assertEqual(not_initial, _events(via_loop))

    def test_never_reads_the_real_clock(self):
        store = self._store()
        with mock.patch("time.time", side_effect=AssertionError("real clock read")):
            ingest(read_jsonl(MORNING), store, stale_ms=STALE_MS)
        self.assertTrue(_events(store))

    def test_no_agent_or_rules(self):
        store = self._store()
        ingest(read_jsonl(MORNING), store, stale_ms=STALE_MS)
        self.assertEqual((_count(store, "agent_runs"), _count(store, "rule_runs")), (0, 0))

    def test_returns_stats(self):
        store = self._store()
        stats = ingest(read_jsonl(MORNING), store, stale_ms=STALE_MS)
        rows = [json.loads(line)["ts"] for line in MORNING.read_text().splitlines() if line.strip()]
        self.assertEqual(stats, IngestStats(snapshots=len(rows), events=len(_events(store)),
                                            first_ts=rows[0], last_ts=rows[-1]))

    def test_empty_input(self):
        store = self._store()
        self.assertEqual(ingest(iter([]), store, stale_ms=STALE_MS), IngestStats(0, 0, None, None))


def at(*args) -> int:
    return int(datetime(*args, tzinfo=TZ).timestamp() * 1000)


def plug(ts: int, watts: int) -> tuple[int, dict]:
    return ts, {"devices": [{"id": "p1", "name": "Plug", "type": "plug", "lastCommunicationTime": ts,
                             "capabilities": [{"type": "instantPower", "value": watts}]}]}


class DayEndTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.store = Store(self.dir, record=False)
        self.addCleanup(self.store.close)

    def test_once_per_date_in_order_including_gaps(self):
        snaps = [plug(at(2026, 9, 1, 8), 0), plug(at(2026, 9, 1, 9), 2100), plug(at(2026, 9, 3, 8), 0)]
        days = []
        ingest(snaps, self.store, stale_ms=STALE_MS, on_day_end=days.append)
        self.assertEqual(days, [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)])

    def test_across_the_clock_change(self):
        snaps = [plug(at(2026, 10, 24, 23, 30), 0), plug(at(2026, 10, 25, 1, 30), 5),
                 plug(at(2026, 10, 25, 23, 30), 0), plug(at(2026, 10, 26, 0, 30), 5)]
        days = []
        ingest(snaps, self.store, stale_ms=STALE_MS, on_day_end=days.append)
        self.assertEqual(days, [date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26)])

    def test_day_is_committed_and_nothing_later_is_stored(self):
        snaps = [plug(at(2026, 9, 1, 8), 0), plug(at(2026, 9, 1, 9), 2100), plug(at(2026, 9, 2, 0, 5), 0)]
        seen = {}

        def check(day):
            other = sqlite3.connect(self.dir / "home.sqlite3")  # sees only committed rows
            seen[day] = other.execute("SELECT COUNT(*), MAX(ts) FROM events").fetchone()
            other.close()

        ingest(snaps, self.store, stale_ms=STALE_MS, on_day_end=check)
        count, latest = seen[date(2026, 9, 1)]
        self.assertEqual(count, 2)  # the plug's initial value and its change
        self.assertLess(latest, day_bounds(date(2026, 9, 1))[1])
        self.assertEqual(seen[date(2026, 9, 2)][0], 3)

    def test_out_of_order_snapshots_rejected(self):
        with self.assertRaises(ValueError):
            ingest([plug(at(2026, 9, 2, 8), 0), plug(at(2026, 9, 1, 8), 5)], self.store, stale_ms=STALE_MS)

    def test_no_hook_still_commits(self):
        ingest([plug(at(2026, 9, 1, 8), 0), plug(at(2026, 9, 1, 9), 2100)], self.store, stale_ms=STALE_MS)
        other = sqlite3.connect(self.dir / "home.sqlite3")
        self.assertEqual(other.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        other.close()



class InitialAndDevicesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name), record=False)
        self.addCleanup(self.store.close)

    def test_one_initial_event_per_signal(self):
        ingest(read_jsonl(MORNING), self.store, stale_ms=STALE_MS)
        first_ts, first_home = next(read_jsonl(MORNING))
        expected = sorted((r.device_id, r.capability, r.value) for r in normalize(first_home).values())
        initial = [e for e in self.store.events_since(-1) if e.kind == "initial"]
        self.assertEqual(sorted((e.device_id, e.capability, e.new) for e in initial), expected)
        self.assertTrue(all(e.ts == first_ts and e.old is None for e in initial))

    def test_devices_found_by_type(self):
        ingest(read_jsonl(MORNING), self.store, stale_ms=STALE_MS)
        _, home = next(read_jsonl(MORNING))
        plugs = [d["id"] for d in home["devices"] if d["type"] == "plug"]
        self.assertEqual(self.store.devices_of_type("plug"), plugs)
        self.assertEqual(self.store.devices_of_type("security"), ["home-security"])

    def test_repaired_device_keeps_its_type(self):
        first, second = plug(at(2026, 9, 1, 8), 0), plug(at(2026, 9, 1, 9), 5)
        second[1]["devices"][0]["id"] = "p2"
        ingest([first, second], self.store, stale_ms=STALE_MS)
        self.assertEqual(self.store.devices_of_type("plug"), ["p1", "p2"])

    def test_initial_event_reads_plainly(self):
        self.assertEqual(Event(0, "p1", "Plug", "instantPower", "initial", None, 0).describe(),
                         "Plug.instantPower starts at 0")


if __name__ == "__main__":
    unittest.main()
