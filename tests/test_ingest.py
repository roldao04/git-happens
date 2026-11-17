"""Ingest: JSONL snapshots → events in the store, at full speed, with every time from the data.

  uv run python -m unittest discover -s tests -t . -v
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from source.agent import StubAgent
from source.ingest import IngestStats, ingest, read_jsonl
from source.loop import run
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
        self.assertEqual(_events(via_ingest), _events(via_loop))

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


if __name__ == "__main__":
    unittest.main()
