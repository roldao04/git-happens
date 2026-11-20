"""The namer (source/learn/namer.py): cards from the LLM, stored on the learned sensors.

  uv run python -m unittest tests.test_namer -v
"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from langchain_core.messages import AIMessage

from source.learn import storage
from source.learn.namer import (name_all, name_habits, name_occasions, name_sensors, parse_cards,
                                reuse_cards, saved_cards)
from source.learn.run import learn_day

FIXTURE = Path(__file__).parent / "fixtures" / "happenings-14d.jsonl"


def learned_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    storage.init(db)
    storage.add_happenings(db, [json.loads(l) for l in FIXTURE.read_text().splitlines()])
    for i in range(14):
        learn_day(db, date(2026, 9, 1) + timedelta(days=i))
    return db


def sensors(db) -> dict[str, dict]:
    return {s["id"]: s for s in storage.export(db)["sensors"]}


class FakeModel:
    """Answers with one card per sensor it was asked about, named by `namer(fact)`."""

    def __init__(self, namer=lambda f: f"Habit {f['id']}", reply: str | None = None):
        self.namer, self.reply, self.requests = namer, reply, []

    def invoke(self, messages):
        request = json.loads(messages[-1].content)
        self.requests.append(request)
        if self.reply is not None:
            return AIMessage(content=self.reply)
        cards = [{"id": f["id"], "name": self.namer(f), "explanation": "e", "means": "m",
                  "if_missing": "x", "importance": "high", "same_as": None} for f in request["to_name"]]
        return AIMessage(content="Sure:\n" + json.dumps({"cards": cards}))


class NameSensorsTest(unittest.TestCase):
    def setUp(self):
        self.db = learned_db()
        self.active = [i for i, s in sensors(self.db).items() if s["status"] in ("active", "fading")]  # named: still shown
        self.assertTrue(self.active)

    def test_stores_a_card_on_every_shown_sensor(self):
        stored = name_sensors(self.db, FakeModel())
        self.assertEqual({c["id"] for c in stored}, set(self.active))
        for i in self.active:
            s = sensors(self.db)[i]
            self.assertEqual((s["name"], s["named_by"], s["importance"]), (f"Habit {i}", "llm", "high"))
            self.assertEqual((s["means"], s["if_missing"]), ("m", "x"))

    def test_sends_facts_only_and_one_call(self):
        model = FakeModel()
        name_sensors(self.db, model)
        self.assertEqual(len(model.requests), 1)
        fact = model.requests[0]["to_name"][0]
        self.assertIn("parts", fact)
        self.assertNotIn("name", fact)  # facts, not old names
        self.assertEqual(name_sensors(self.db, model), [])  # nothing left to name: no second call
        self.assertEqual(len(model.requests), 1)

    def test_keeps_a_persons_name(self):
        i = self.active[0]
        data = sensors(self.db)[i] | {"name": "Rosa's tea", "named_by": "person"}
        self.db.execute("UPDATE sensors SET data = ? WHERE id = ?", (json.dumps(data), i))
        model = FakeModel()
        name_sensors(self.db, model, again=True)
        self.assertEqual(sensors(self.db)[i]["name"], "Rosa's tea")
        self.assertNotIn(i, {f["id"] for f in model.requests[0]["to_name"]})
        self.assertIn("Rosa's tea", {f["name"] for f in model.requests[0]["already_named"]})

    def test_again_renames_only_llm_names(self):
        name_sensors(self.db, FakeModel())
        name_sensors(self.db, FakeModel(lambda f: f"New {f['id']}"), again=True)
        self.assertTrue(all(sensors(self.db)[i]["name"].startswith("New ") for i in self.active))

    def test_broken_reply_leaves_patterns(self):
        for reply in ("no json here", '{"cards": "nope"}', '{"cards": [{"id": "pattern-1"}]}'):
            self.assertEqual(name_sensors(self.db, FakeModel(reply=reply)), [])
        self.assertTrue(all(sensors(self.db)[i]["name"] is None for i in self.active))

    def test_retries_once_for_cards_left_out(self):
        first = self.active[0]

        class Partial(FakeModel):  # the first reply repeats a name for one sensor, the second fixes it
            def invoke(self, messages):
                msg = super().invoke(messages)
                if len(self.requests) == 1:
                    cards = json.loads(msg.content.split("\n", 1)[1])["cards"]
                    for c in cards:
                        if c["id"] != first:
                            c["name"] = f"Habit {first}"  # already used by the first card: rejected
                    return AIMessage(content=json.dumps({"cards": cards}))
                return msg
        model = Partial()
        stored = name_sensors(self.db, model)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual({f["id"] for f in model.requests[1]["to_name"]}, set(self.active) - {first})
        self.assertIn(f"Habit {first}", {f["name"] for f in model.requests[1]["already_named"]})
        self.assertEqual({c["id"] for c in stored}, set(self.active))

    def test_failing_model_never_raises(self):
        class Down:
            def invoke(self, messages):
                raise ConnectionError("openrouter down")
        self.assertEqual(name_sensors(self.db, Down()), [])

    def test_a_changed_pattern_loses_its_card(self):
        name_sensors(self.db, FakeModel())
        i = self.active[0]
        self.db.execute("UPDATE sensors SET signature = 'kind:other' WHERE id = ?", (i,))
        for d in range(14):
            learn_day(self.db, date(2026, 9, 1) + timedelta(days=d))
        s = sensors(self.db)[i]
        if s["status"] == "active":  # matched again under its real signature: renamed later
            self.assertIsNone(s["name"])
            self.assertIsNone(s.get("means"))


class ReuseCardsTest(unittest.TestCase):
    """Named once, then reused: another run of the same home gets the same names without asking the LLM."""

    def test_another_run_reuses_names_and_asks_only_for_new_sensors(self):
        first = learned_db()
        name_sensors(first, FakeModel())
        cards = saved_cards(first)
        self.assertTrue(cards)

        again = learned_db()
        self.assertEqual(reuse_cards(again, cards), len(cards))
        model = FakeModel(lambda f: f"Other {f['id']}")
        self.assertEqual(name_sensors(again, model), [])  # nothing new: no LLM call
        self.assertEqual(model.requests, [])
        self.assertEqual({i: s["name"] for i, s in sensors(again).items()},
                         {i: s["name"] for i, s in sensors(first).items()})

    def test_a_changed_pattern_is_not_reused(self):
        first = learned_db()
        name_sensors(first, FakeModel())
        cards = saved_cards(first)
        again = learned_db()
        i = next(iter(sensors(again)))
        again.execute("UPDATE sensors SET signature = 'kind:other' WHERE id = ?", (i,))
        reuse_cards(again, cards)
        self.assertIsNone(sensors(again)[i]["name"])

    def test_a_persons_name_is_kept(self):
        first = learned_db()
        name_sensors(first, FakeModel())
        again = learned_db()
        i = next(iter(sensors(again)))
        data = sensors(again)[i] | {"name": "Rosa's tea", "named_by": "person"}
        again.execute("UPDATE sensors SET data = ? WHERE id = ?", (json.dumps(data), i))
        reuse_cards(again, saved_cards(first))
        self.assertEqual(sensors(again)[i]["name"], "Rosa's tea")


class HabitsAndOccasionsTest(unittest.TestCase):
    """Habits (and occasions, when there are any) are named like patterns, with their own facts and keys."""

    def setUp(self):
        self.db = learned_db()  # learn_day also learns habits and occasions
        self.habits = [json.loads(d) for (d,) in self.db.execute("SELECT data FROM habits")
                       if json.loads(d)["status"] in ("active", "fading")]
        self.assertTrue(self.habits)

    def test_habits_get_cards_from_their_rhythm_and_steps(self):
        model = FakeModel()
        stored = name_habits(self.db, model)
        self.assertEqual({c["id"] for c in stored}, {h["id"] for h in self.habits})
        fact = model.requests[0]["to_name"][0]
        self.assertIn("recurrence", fact)
        self.assertTrue(fact["steps"] and "parts" in fact["steps"][0])
        h = json.loads(self.db.execute("SELECT data FROM habits WHERE id = ?", (stored[0]["id"],)).fetchone()[0])
        self.assertEqual((h["name"], h["named_by"]), (f"Habit {h['id']}", "llm"))

    def test_steps_show_the_patterns_names(self):
        name_sensors(self.db, FakeModel())
        model = FakeModel()
        name_habits(self.db, model)
        names = {st.get("name") for f in model.requests[0]["to_name"] for st in f["steps"]}
        self.assertTrue(any(n and n.startswith("Habit pattern-") for n in names))

    def test_name_all_and_reuse_cover_habits(self):
        name_all(self.db, FakeModel())
        cards = saved_cards(self.db)
        self.assertTrue(any(k.startswith("habit:") for k in cards))
        again = learned_db()
        reuse_cards(again, cards)
        model = FakeModel()
        self.assertEqual(name_all(again, model), [])  # all reused: no LLM call
        self.assertEqual(model.requests, [])

    def test_occasions_without_any_ask_nothing(self):
        model = FakeModel()
        self.assertEqual(name_occasions(sqlite3.connect(":memory:"), model), [])
        self.assertEqual(model.requests, [])


class NamedModel(FakeModel):
    model_name = "test/namer"  # a model with a name goes through the reply cache (source/llm_cache.py)


class CacheTest(unittest.TestCase):
    def test_the_same_question_is_answered_from_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"ALFRED_LLM_CACHE_DIR": tmp}):
            os.environ.pop("ALFRED_LLM_CACHE", None)
            first = NamedModel()
            names = {c["id"]: c["name"] for c in name_sensors(learned_db(), first)}
            self.assertTrue(first.requests)

            os.environ["ALFRED_LLM_CACHE"] = "only"  # the demo: no network
            again = NamedModel(namer=lambda f: "something else")
            self.assertEqual({c["id"]: c["name"] for c in name_sensors(learned_db(), again)}, names)
            self.assertEqual(again.requests, [])

    def test_only_with_nothing_cached_names_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"ALFRED_LLM_CACHE_DIR": tmp, "ALFRED_LLM_CACHE": "only"}):
            model = NamedModel()
            with self.assertLogs("source.learn.namer", "ERROR"):
                self.assertEqual(name_sensors(learned_db(), model), [])  # fails like a broken LLM: no names
            self.assertEqual(model.requests, [])


class ParseCardsTest(unittest.TestCase):
    def card(self, **kw):
        return {"id": "pattern-1", "name": "Morning kettle", "explanation": " Most  mornings. ",
                "means": "Up.", "if_missing": "Check.", "importance": "high", "same_as": None} | kw

    def parse(self, *cards, taken=()):
        return parse_cards(json.dumps({"cards": list(cards)}), {"pattern-1", "pattern-2"}, set(taken))

    def test_clean_card(self):
        c = self.parse(self.card())["pattern-1"]
        self.assertEqual(c["explanation"], "Most mornings.")
        self.assertEqual(c["importance"], "high")

    def test_rejects_unknown_ids_long_names_and_repeats(self):
        self.assertEqual(self.parse(self.card(id="pattern-9")), {})
        self.assertEqual(self.parse(self.card(name="A very long name for a simple morning kettle")), {})
        self.assertEqual(self.parse(self.card(name="Morning kettle")), {"pattern-1": self.parse(self.card())["pattern-1"]})
        self.assertEqual(self.parse(self.card(), taken=["morning kettle"]), {})
        both = self.parse(self.card(), self.card(id="pattern-2"))
        self.assertEqual(set(both), {"pattern-1"})  # the second "Morning kettle" is a repeat

    def test_defaults_unknown_importance_and_self_reference(self):
        c = self.parse(self.card(importance="urgent", same_as="pattern-1"))["pattern-1"]
        self.assertEqual((c["importance"], c["same_as"]), ("normal", None))
        self.assertEqual(self.parse(self.card(same_as="pattern-2"))["pattern-1"]["same_as"], "pattern-2")


if __name__ == "__main__":
    unittest.main()


class ExpectationsTest(unittest.TestCase):
    """The namer sees what a habit expects (source/learn/expectations.py) and may quiet one."""

    EXPECTS = [{"device_id": "contact-1", "device": "Living-room window", "device_type": "contactSensor",
                "capability": "sensorState", "value": "normal", "agree": 6, "of": 6}]

    def setUp(self):
        from source.learn import grouping
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        storage.init(self.db)
        self.db.executescript(grouping.SCHEMA)
        habit = {"id": "habit-1", "name": None, "named_by": None, "status": "active", "patterns": [], "steps": [],
                 "expects": self.EXPECTS}
        self.db.execute("INSERT INTO habits (id, status, data) VALUES ('habit-1', 'active', ?)", (json.dumps(habit),))

    def habit(self) -> dict:
        return json.loads(self.db.execute("SELECT data FROM habits").fetchone()[0])

    def reply(self, quiet) -> str:
        return json.dumps({"cards": [{"id": "habit-1", "name": "Night", "importance": "high", "quiet": quiet}]})

    def test_habit_facts_show_expectations_by_device_name(self):
        from source.learn.namer import habit_facts
        facts = habit_facts(self.habit(), {})
        self.assertEqual(facts["expects"], [{"device": "Living-room window", "value": "normal", "agree": 6, "of": 6}])

    def test_a_card_can_quiet_an_expectation(self):
        name_habits(self.db, FakeModel(reply=self.reply(["Living-room window"])))
        self.assertEqual(self.habit()["quiet_expects"], ["Living-room window"])

    def test_quiet_names_not_in_the_habit_are_dropped(self):
        name_habits(self.db, FakeModel(reply=self.reply(["Kitchen door", 3])))
        self.assertEqual(self.habit()["quiet_expects"], [])

    def test_a_card_without_quiet_quiets_nothing(self):
        name_habits(self.db, FakeModel(reply=json.dumps({"cards": [{"id": "habit-1", "name": "Night"}]})))
        self.assertEqual(self.habit().get("quiet_expects", []), [])
