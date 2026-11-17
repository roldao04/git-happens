import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from source.clock import TZ, day_bounds, local_day

LISBON = ZoneInfo("Europe/Lisbon")


def ms(*args) -> int:
    return int(datetime(*args, tzinfo=LISBON).timestamp() * 1000)


class ClockTest(unittest.TestCase):
    def test_default_zone_is_lisbon(self):
        self.assertEqual(TZ, LISBON)

    def test_local_day_uses_lisbon_not_the_machine(self):
        self.assertEqual(local_day(ms(2026, 9, 1, 0, 30)), date(2026, 9, 1))  # 23:30 UTC the day before
        self.assertEqual(local_day(ms(2026, 9, 1, 23, 59)), date(2026, 9, 1))

    def test_day_bounds_are_local_midnights(self):
        start, end = day_bounds(date(2026, 9, 1))
        self.assertEqual((start, end), (ms(2026, 9, 1), ms(2026, 9, 2)))

    def test_day_the_clocks_go_back_has_25_hours(self):
        start, end = day_bounds(date(2026, 10, 25))
        self.assertEqual(end - start, 25 * 3600_000)


if __name__ == "__main__":
    unittest.main()
