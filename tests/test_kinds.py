"""The grouper (part 5): kinds and links on hand-made happenings.

  uv run python -m unittest tests.test_kinds -v
"""
import unittest
from datetime import timedelta

from source.learn import Happening, is_activity
from source.learn.kinds import find_kinds, find_links
from source.learn.rhythm import Rhythm
from tests.fixtures.make_happenings import DOOR, HOUSEHOLD_A, PLUG, independent_kettle, make


def happenings(household=HOUSEHOLD_A, days=14):
    return [Happening.from_dict(r) for r in make(household, days)]


def context(hs):
    first = min(h.start for h in hs).date()
    last = max(h.start for h in hs).date()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    act = [h for h in hs if is_activity(h)]
    return days, Rhythm([h.start for h in act] + [h.end for h in act])


def kind_of(kinds, h):
    return next(k for k in kinds if k.id == h.kind)


class KindsTest(unittest.TestCase):
    def test_kettle_weekly_and_noise_are_separate_kinds(self):
        hs = happenings(days=42)
        kinds = find_kinds(hs)
        kettle = {h.kind for h in hs if h.signal == PLUG and h.size > 1900}
        weekly = {h.kind for h in hs if h.signal == PLUG and h.size < 700}
        noise = {h.kind for h in hs if h.signal == PLUG and 800 <= h.size <= 1200}
        self.assertEqual(len(kettle), 1)
        self.assertEqual(len(weekly), 1)
        self.assertTrue(kettle.isdisjoint(noise) and weekly.isdisjoint(noise) and kettle != weekly)
        k = next(k for k in kinds if k.id in kettle)
        self.assertAlmostEqual(k.size, 2100, delta=50)
        self.assertAlmostEqual(k.duration_s, 180, delta=20)

    def test_too_few_get_no_kind(self):
        hs = happenings(days=14)  # only 2 Thursdays
        find_kinds(hs)
        self.assertTrue(all(h.kind is None for h in hs if h.signal == PLUG and h.size < 700))

    def test_ids_stay_when_days_are_added(self):
        before = happenings(days=14)
        kinds = find_kinds(before)
        kettle_id = next(h.kind for h in before if h.signal == PLUG and h.size > 1900)
        after = happenings(days=21)
        find_kinds(after, kinds)
        self.assertEqual({h.kind for h in after if h.signal == PLUG and h.size > 1900}, {kettle_id})


class LinksTest(unittest.TestCase):
    def links(self, household, null):
        hs = happenings(household, 14)
        kinds = find_kinds(hs)
        days, rhythm = context(hs)
        kettle = next(h.kind for h in hs if h.signal == PLUG and h.size > 1900)
        return [l for l in find_links(hs, kinds, days, rhythm, null) if l.b == kettle]

    def test_door_then_kettle_is_linked_under_both_chance_models(self):
        for null in ("rhythm", "other_day"):
            with self.subTest(null=null):
                links = self.links(HOUSEHOLD_A, null)
                self.assertEqual(len(links), 1)
                link = links[0]
                self.assertTrue(link.a == DOOR or link.a.startswith("contactSensor-"))
                self.assertGreaterEqual(len(link.pairs), 11)
                self.assertTrue(3 <= link.lag_min <= 8)

    def test_door_and_kettle_on_separate_clocks_are_not_linked(self):
        # both happen around 08:00, but the kettle doesn't follow the door
        for null in ("rhythm", "other_day"):
            with self.subTest(null=null):
                self.assertEqual(self.links(independent_kettle(HOUSEHOLD_A), null), [])


if __name__ == "__main__":
    unittest.main()
