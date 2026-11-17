"""Home time: days and times of day in the home's zone, never the machine's."""
from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Lisbon")


def local_day(ms: int, tz: tzinfo = TZ) -> date:
    return datetime.fromtimestamp(ms / 1000, tz).date()


def day_bounds(day: date, tz: tzinfo = TZ) -> tuple[int, int]:
    """[start, end) of a local day in epoch ms; 23 or 25 hours long when the clocks change."""
    start = datetime.combine(day, time(), tz)
    end = datetime.combine(day + timedelta(days=1), time(), tz)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
