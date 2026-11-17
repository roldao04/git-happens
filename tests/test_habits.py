"""The habit learner (part 6): the planted routines are found, noise and chance are not.

  uv run python -m unittest tests.test_habits -v
"""
import random
import unittest
from datetime import datetime, timedelta

from source.learn import Happening
from source.learn.run import learn
from tests.fixtures.make_happenings import HOUSEHOLD_A, HOUSEHOLD_B, PLUG, make


def happenings(household=HOUSEHOLD_A, days=14):
    return [Happening.from_dict(r) for r in make(household, days)]


def habits(hs):
    last = max(h.start for h in hs).date()
    return {h.key: h for h in learn(hs, last).habits}


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def near(found: dict, prefix: str, hhmm: str, within: int = 20) -> list:
    """Habits whose key starts with `prefix` (e.g. "kind:home-": a kind of quiet) around a clock time."""
    return [h for k, h in found.items() if k.startswith(prefix)
            and abs(minutes(h.pattern()["time_of_day"]) - minutes(hhmm)) <= within]


class HabitsTest(unittest.TestCase):
    def assertAround(self, habit, hhmm, within=20):
        got = minutes(habit.pattern()["time_of_day"])
        self.assertLessEqual(abs(got - minutes(hhmm)), within, habit.pattern())

    def test_household_a_two_weeks(self):
        found = habits(happenings())
        link = next(h for k, h in found.items() if k.startswith("link:"))
        self.assertIn("2,100 W", link.parts[1])
        self.assertAround(link, "07:58")
        self.assertGreaterEqual(link.days_seen, 11)
        self.assertEqual(link.days_total, 14)
        self.assertTrue(near(found, "kind:home-", "23:15"), list(found))  # the night's quiet (asleep)
        self.assertTrue(near(found, "kind-end:home-", "07:00"), list(found))  # its end (awake)
        self.assertNotIn(f"kind:{link.parts[1].split()[0]}", found)  # the kettle is inside the link
        self.assertFalse(any(h.weekdays for h in found.values()))  # 2 Thursdays are not enough

    def test_noise_makes_no_habit(self):
        hs = happenings(days=42)
        found = habits(hs)
        noise = {h.kind for h in hs if h.signal == PLUG and 800 <= h.size <= 1200}
        door_kinds = {h.kind for h in hs if h.signal.startswith("contactSensor")}
        self.assertTrue(all(f"kind:{k}" not in found for k in noise | door_kinds))

    def test_thursday_habit_after_six_weeks(self):
        found = habits(happenings(days=42))
        weekly = [h for h in found.values() if h.weekdays]
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0].weekdays, ["thu"])
        self.assertIn("650 W", weekly[0].parts[0])
        self.assertAround(weekly[0], "10:00")
        self.assertEqual((weekly[0].days_seen, weekly[0].days_total), (6, 6))

    def test_night_shift_home_gets_different_sensors(self):
        found = habits(happenings(HOUSEHOLD_B))
        self.assertTrue(near(found, "kind:home-", "08:00"), list(found))  # asleep in the morning
        self.assertTrue(near(found, "kind-end:home-", "15:00"), list(found))  # up in the afternoon
        link = next(h for k, h in found.items() if k.startswith("link:"))
        self.assertIn("1,400 W", link.parts[1])  # wake light → coffee, no door → kettle

    def test_random_times_make_no_habits(self):
        rng = random.Random(7)
        hs = []
        for h in happenings(days=42):
            day = datetime.combine(h.start.date(), datetime.min.time())
            start = day + timedelta(minutes=rng.uniform(0, 1440))
            hs.append(Happening(h.signal, start, start + (h.end - h.start), h.size, h.value))
        self.assertEqual(habits(hs), {})


if __name__ == "__main__":
    unittest.main()
