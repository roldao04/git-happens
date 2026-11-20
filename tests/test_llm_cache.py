"""The LLM reply cache (source/llm_cache.py, docs/contracts.md C10). No network.

  uv run python -m unittest tests.test_llm_cache -v
"""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from source import llm_cache


class Cached(unittest.TestCase):
    """A temporary cache folder, and ALFRED_LLM_CACHE as each test sets it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.enterContext(mock.patch.dict(os.environ, {"ALFRED_LLM_CACHE_DIR": tmp.name}))
        os.environ.pop("ALFRED_LLM_CACHE", None)
        self.calls = 0

    def call(self, reply="hello"):
        def fn():
            self.calls += 1
            return reply
        return fn


class CacheTest(Cached):
    def test_a_hit_never_calls_the_model(self):
        self.assertEqual(llm_cache.cached("agent", {"a": 1}, self.call()), "hello")
        self.assertEqual(llm_cache.cached("agent", {"a": 1}, self.call("other")), "hello")
        self.assertEqual(self.calls, 1)
        (line,) = (self.dir / "agent.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(line)["reply"], "hello")

    def test_key_is_kind_and_sorted_payload(self):
        self.assertEqual(llm_cache.key("agent", {"a": 1, "b": 2}), llm_cache.key("agent", {"b": 2, "a": 1}))
        self.assertNotEqual(llm_cache.key("agent", {"a": 1}), llm_cache.key("namer", {"a": 1}))
        llm_cache.cached("agent", {"a": 1}, self.call())
        llm_cache.cached("namer", {"a": 1}, self.call())
        llm_cache.cached("agent", {"a": 2}, self.call())
        self.assertEqual(self.calls, 3)
        self.assertTrue((self.dir / "namer.jsonl").exists())

    def test_only_fails_instead_of_calling(self):
        llm_cache.cached("agent", {"a": 1}, self.call())
        with mock.patch.dict(os.environ, {"ALFRED_LLM_CACHE": "only"}):
            self.assertEqual(llm_cache.cached("agent", {"a": 1}, self.call()), "hello")
            with self.assertRaises(llm_cache.CacheMiss):
                llm_cache.cached("agent", {"a": 2}, self.call())
        self.assertEqual(self.calls, 1)

    def test_off_never_reads_but_refreshes(self):
        llm_cache.cached("agent", {"a": 1}, self.call("old"))
        with mock.patch.dict(os.environ, {"ALFRED_LLM_CACHE": "off"}):
            self.assertEqual(llm_cache.cached("agent", {"a": 1}, self.call("new")), "new")
        self.assertEqual(llm_cache.cached("agent", {"a": 1}, self.call("x")), "new")  # the last line wins
        self.assertEqual(self.calls, 2)

    def test_a_broken_line_is_ignored(self):
        llm_cache.cached("agent", {"a": 1}, self.call())
        with (self.dir / "agent.jsonl").open("a") as f:
            f.write('{"key": "half')
        self.assertEqual(llm_cache.cached("agent", {"a": 1}, self.call("x")), "hello")

    def test_async(self):
        async def fn():
            self.calls += 1
            return "async"
        self.assertEqual(asyncio.run(llm_cache.acached("agent", {"a": 1}, fn)), "async")
        self.assertEqual(asyncio.run(llm_cache.acached("agent", {"a": 1}, fn)), "async")
        self.assertEqual(self.calls, 1)

    def test_model_name(self):
        class Named:
            model_name = "anthropic/claude-sonnet-4.5"
        self.assertEqual(llm_cache.model_name(Named()), "anthropic/claude-sonnet-4.5")
        self.assertIsNone(llm_cache.model_name(object()))  # a fake model: never cached


if __name__ == "__main__":
    unittest.main()
