"""The 3D view's data: one day of a household, cut down to what the page draws, and the routes that serve it.

  uv run python -m unittest tests.test_visualizer -v
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime
from http.server import ThreadingHTTPServer

from source.simulate import REAL_HOME, TZ, generate, houses
from source.simulate_web import (MAX_DAYS, REAL_MAX_DAYS, VIEW_PAGE, WATCHED, Handler, _day_request, compact,
                                 day_view)

START = date(2026, 9, 1)


def _replay(view: dict) -> list[tuple[int, list[dict]]]:
    """The frames put back together, as the page does: each snapshot's time and every device's state."""
    out, state = [], {}
    for ts, changes in view["frames"]:
        state = {**state, **{int(n): {**state.get(int(n), {}), **c} for n, c in changes.items()}}
        out.append((ts, [state[n] for n in sorted(state)]))
    return out


def _day_of(household, day: int) -> list[dict]:
    rows = [json.loads(line) for line in generate(household, day, start=START)[0]]
    return [r for r in rows if datetime.fromtimestamp(r["ts"] / 1000, TZ).date() == date(2026, 9, day)]


class DayViewTest(unittest.TestCase):
    def test_frames_replay_to_every_snapshot_of_that_day(self):
        for name, day in (("rosa", 1), ("students", 3), (REAL_HOME, 2)):
            view = json.loads(json.dumps(day_view(name, day)))  # as the page gets it: keys are strings
            rows = _day_of(name, day)
            self.assertEqual(_replay(view), [(r["ts"], compact(r["home"])) for r in rows], name)
            self.assertEqual((view["start"], view["end"] - view["start"]), (rows[0]["ts"], 86_400_000), name)
            self.assertEqual(view["date"], f"2026-09-{day:02d}")

    def test_first_frame_is_whole_and_the_rest_are_only_changes(self):
        view = day_view("rosa", 1)
        first, later = view["frames"][0][1], [changes for _, changes in view["frames"][1:]]
        self.assertEqual(len(first), len(view["devices"]) + 1)  # every device, then the home's security
        self.assertTrue(any(not changes for changes in later))  # a quiet snapshot is still a frame
        self.assertTrue(all(len(changes) < len(first) for changes in later))

    def test_keeps_only_what_the_view_draws(self):
        view = day_view("rosa", 1)
        self.assertEqual([d["type"] for d in view["devices"]],
                         ["temperatureSensor", "contactSensor", "gateway", "smokeSensor", "plug", "bulb", "camera"])
        self.assertEqual(set(view["devices"][0]), {"id", "name", "type", "room"})
        for n, dev in enumerate(view["devices"]):
            self.assertLessEqual(set(view["frames"][0][1][n]), set(WATCHED.get(dev["type"], ())))
        self.assertEqual(view["frames"][0][1][len(view["devices"])], {"status": "DISARMED", "isAlarmed": False})
        self.assertLess(len(json.dumps(view)), 40_000)  # the raw day is about 1.7 MB

    def test_the_day_shows_its_habits(self):
        view = day_view("rosa", 1)
        types = [d["type"] for d in view["devices"]]
        seen = {(types[int(n)], cap, value) for _, changes in view["frames"][1:] for n, c in changes.items()
                if int(n) < len(types) for cap, value in c.items()}
        self.assertIn(("contactSensor", "sensorState", "alarm"), seen)
        self.assertIn(("bulb", "power", "on"), seen)
        self.assertTrue(any(t == "plug" and cap == "instantPower" and value > 1500 for t, cap, value in seen))

    def test_a_made_up_house_is_the_one_in_the_forms_zip(self):
        rows = _day_of(houses(3)[2], 2)
        self.assertEqual(_replay(json.loads(json.dumps(day_view("house-3", 2)))),
                         [(r["ts"], compact(r["home"])) for r in rows])

    def test_answer_key_is_only_that_day(self):
        truth = day_view("rosa", 2)["truth"]
        self.assertIn("wake", [t["what"] for t in truth])
        self.assertTrue(all(t["start"].startswith("2026-09-02") for t in truth))

    def test_reads_the_query(self):
        self.assertEqual(_day_request({}), ("rosa", 1, 1, START))
        self.assertEqual(_day_request({"household": "couple", "day": "7", "seed": "3", "start": "2026-10-05"}),
                         ("couple", 7, 3, date(2026, 10, 5)))
        self.assertEqual(_day_request({"household": REAL_HOME, "day": str(REAL_MAX_DAYS)})[1], REAL_MAX_DAYS)

    def test_bad_input_is_a_plain_error(self):
        for bad in ({"household": "nobody"}, {"household": "house-0"}, {"household": "house-21"}, {"day": "0"},
                    {"day": "two"}, {"day": str(MAX_DAYS + 1)}, {"start": "yesterday"}, {"seed": "x"},
                    {"household": REAL_HOME, "day": str(REAL_MAX_DAYS + 1)}):
            with self.assertRaises(ValueError, msg=bad):
                _day_request(bad)


class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str, **headers) -> tuple[int, str, bytes]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.status, res.headers["Content-Type"], res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers["Content-Type"], err.read()

    def test_serves_the_page(self):
        status, kind, body = self._get("/visualizer")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"))
        self.assertEqual(body, VIEW_PAGE.read_bytes())
        self.assertIn(b"/ajax/libs/three.js/r128/three.min.js", body)  # an exact version, with its hash
        self.assertIn(b'integrity="sha384-', body)

    def test_the_form_links_to_the_page(self):
        self.assertIn(b'href="/visualizer"', self._get("/")[2])

    def test_serves_a_day(self):
        status, kind, body = self._get("/day?household=couple&day=2")
        self.assertEqual((status, kind), (200, "application/json"))
        self.assertEqual(json.loads(body), json.loads(json.dumps(day_view("couple", 2))))

    def test_bad_day_is_a_plain_error(self):
        status, _, body = self._get("/day?household=nobody")
        self.assertEqual(status, 400)
        self.assertIn("unknown household", json.loads(body)["error"])
        self.assertEqual(self._get("/day/more")[0], 404)

    def test_refuses_other_hosts_and_sites(self):
        self.assertEqual(self._get("/day", Host="evil.example")[0], 403)
        self.assertEqual(self._get("/visualizer", Host="evil.example")[0], 403)
        self.assertEqual(self._get("/day", Origin="http://evil.example")[0], 403)


if __name__ == "__main__":
    unittest.main()
