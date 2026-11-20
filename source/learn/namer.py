"""Part 7, the namer: names what the learners found and says what it means (docs/contracts.md).

The learners know *that* something recurs, never *what* it is. Three things get names, each in one call per
run (so the LLM sees them side by side and can spot repeats):

  patterns    the learners' sensors: one kind or pair at a consistent time   (prompts/namer.md)
  habits      patterns that recur together in a rhythm: "Waking up"          (prompts/namer-habits.md)
  occasions   rare departures recognised by resemblance: "Evening gathering" (prompts/namer-occasions.md)

Each gets a card:

  name, explanation    what people see on the dashboard ("Morning kettle")
  means, if_missing    what the agent reasons with when the habit happens or doesn't
  importance, same_as  high | normal | low | hidden, and the card a duplicate repeats

Names describe only what the data can tell apart: "Morning kettle", never "Morning tea".
A failed call changes nothing: the sensor stays "Pattern N". A person's name is never replaced.

Habits and occasions are what people see; a pattern's name is the detail under a habit.

  uv run python -m source.learn.namer --db data/sim/rosa/home.sqlite3
  uv run python -m source.learn.namer --db data/sim/rosa/home.sqlite3 --again   # rename the LLM's names too
"""
import argparse
import json
import logging
import sqlite3
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from .. import llm_cache

log = logging.getLogger("source.learn.namer")

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_PATH = PROMPTS / "namer.md"
CARD_FIELDS = ("name", "explanation", "means", "if_missing", "importance", "same_as")
IMPORTANCE = ("high", "normal", "low", "hidden")
FACTS = ("parts", "time_of_day", "earliest", "latest", "spread_min", "weekdays", "days_seen", "days_total",
         "lag_min", "until", "duration_min", "after_quiet_min")
MAX_NAME_WORDS = 5
MAX_NAME_CHARS = 40
ATTEMPTS = 2  # a second call for the sensors whose card was missing or rejected


def _devices(db: sqlite3.Connection) -> dict[str, list[str]]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'devices'").fetchone():
        return {}
    out: dict[str, list[str]] = {}
    for t, n in db.execute("SELECT type, name FROM devices ORDER BY type, name"):
        out.setdefault(t, []).append(n)
    return out


def facts(sensor: dict) -> dict:
    """What the LLM may read about a sensor: its pattern and confidence, nothing else."""
    p = sensor.get("pattern", {})
    return {"id": sensor["id"], **{k: p[k] for k in FACTS if k in p},
            "confidence": sensor.get("confidence")}


def parse_cards(text: str, ids: set[str], taken: set[str]) -> dict[str, dict]:
    """The LLM's reply as {sensor id: card}. Invalid cards are left out; never raises."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        raw = json.loads(text[start:end + 1]).get("cards", [])
    except (ValueError, AttributeError):
        return {}
    out: dict[str, dict] = {}
    names = {n.casefold() for n in taken}
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or c.get("id") not in ids or c["id"] in out:
            continue
        name = c.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = " ".join(name.split())
        if len(name) > MAX_NAME_CHARS or len(name.split()) > MAX_NAME_WORDS or name.casefold() in names:
            log.warning("namer: rejected %r for %s (%s)", name, c["id"],
                        "already used" if name.casefold() in names else "too long")
            continue
        names.add(name.casefold())
        same_as = c.get("same_as")
        out[c["id"]] = {
            "name": name,
            "explanation": _text(c.get("explanation")),
            "means": _text(c.get("means")),
            "if_missing": _text(c.get("if_missing")),
            "importance": c.get("importance") if c.get("importance") in IMPORTANCE else "normal",
            "same_as": same_as if isinstance(same_as, str) and same_as != c["id"] else None,
        }
        if isinstance(c.get("quiet"), list):
            out[c["id"]]["quiet_expects"] = [q for q in c["quiet"] if isinstance(q, str)]
    return out


def _text(v) -> str | None:
    return " ".join(v.split()) if isinstance(v, str) and v.strip() else None


def _content(msg) -> str:
    content = getattr(msg, "content", msg)
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) if isinstance(b, dict) else ""
                       for b in content)
    return content if isinstance(content, str) else ""


def _table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _rows(db: sqlite3.Connection, table: str) -> list[dict]:
    return [json.loads(d) for (d,) in db.execute(f"SELECT data FROM {table} ORDER BY id")] if _table(db, table) else []


def _pattern_parts(db: sqlite3.Connection) -> dict[str, dict]:
    """Each pattern as a habit's step shows it to the LLM: what it is made of, and its name if it has one."""
    out = {}
    for s in _rows(db, "sensors"):
        p = s.get("pattern", {})
        out[s["id"]] = {"parts": p.get("parts"), "usual": p.get("time_of_day"), "name": s.get("name")}
    return out


def habit_facts(habit: dict, patterns: dict[str, dict]) -> dict:
    """What the LLM may read about a habit: its rhythm, window and steps (each step's pattern in words)."""
    return {"id": habit["id"], "recurrence": (habit.get("recurrence") or {}).get("text"),
            "window": habit.get("window"), "variation": habit.get("variation"), "trend": habit.get("trend"),
            "steps": [{**patterns.get(st.get("pattern"), {}), "offset_min": st.get("offset_min"),
                       "share": st.get("share")} for st in habit.get("steps", [])],
            "occurrences": habit.get("occurrences"), "opportunities": habit.get("opportunities"),
            "confidence": habit.get("confidence"),
            "expects": [{k: e.get(k) for k in ("device", "value", "agree", "of")}
                        for e in habit.get("expects") or [] if isinstance(e, dict)]}


def occasion_facts(occasion: dict, names: dict[str, str]) -> dict:
    """What the LLM may read about an occasion: how it differs from the usual day, and what it moves."""
    return {"id": occasion["id"], "episodes": occasion.get("episodes"), "fingerprint": occasion.get("fingerprint"),
            "usual_hours": occasion.get("usual_hours"), "weekdays": occasion.get("weekdays"),
            "changes": [{**c, "pattern": names.get(c.get("pattern"), c.get("pattern"))}
                        for c in occasion.get("changes", [])],
            "first_seen": occasion.get("first_seen"), "last_seen": occasion.get("last_seen"),
            "confidence": occasion.get("confidence")}


class _Thing:
    """One kind of thing the namer names: where it is stored, which ones are shown, how the LLM sees it."""

    def __init__(self, label: str, table: str, prompt: str, shown: tuple[str, ...]):
        self.label, self.table, self.prompt, self.shown = label, table, prompt, shown

    def items(self, db: sqlite3.Connection) -> list[dict]:
        return [r for r in _rows(db, self.table) if r.get("status", "active") in self.shown]

    def facts(self, db: sqlite3.Connection):
        if self.table == "habits":
            patterns = _pattern_parts(db)
            return lambda h: habit_facts(h, patterns)
        if self.table == "occasions":
            names = {sid: (p["name"] or p["usual"]) for sid, p in _pattern_parts(db).items()}
            names |= {h["id"]: h.get("name") or h["id"] for h in _rows(db, "habits")}
            return lambda o: occasion_facts(o, names)
        return facts

    def key(self, db: sqlite3.Connection, item: dict) -> str:
        """Same key, same thing: a changed pattern or habit has another key and is named again."""
        if self.table == "sensors":
            (sig,) = db.execute("SELECT signature FROM sensors WHERE id = ?", (item["id"],)).fetchone()
            return f"{item['id']}|{sig}"
        if self.table == "habits":
            return f"habit:{item['id']}|{','.join(sorted(item.get('patterns', [])))}"
        return f"occasion:{item['id']}"


PATTERNS = _Thing("pattern", "sensors", "namer.md", ("active", "fading"))  # fading: still shown, still a habit
HABITS = _Thing("habit", "habits", "namer-habits.md", ("active", "fading"))
OCCASIONS = _Thing("occasion", "occasions", "namer-occasions.md", ("active",))
THINGS = (PATTERNS, HABITS, OCCASIONS)


def _name(db: sqlite3.Connection, model, thing: _Thing, again: bool) -> list[dict]:
    """Names every shown `thing` without a name (with `again`, also those the LLM named before).
    Returns the cards stored. Never raises: a broken LLM leaves them as they were."""
    if not _table(db, thing.table):
        return []
    items = thing.items(db)
    to_name = [s for s in items if s.get("named_by") is None or (again and s.get("named_by") == "llm")]
    if not to_name:
        return []
    named = [s for s in items if s not in to_name and s.get("name")]
    cards: dict[str, dict] = {}
    try:
        see, devices, prompt = thing.facts(db), _devices(db), (PROMPTS / thing.prompt).read_text()
        for _ in range(ATTEMPTS):  # the second call only for what the first left out, with its names as taken
            left = [s for s in to_name if s["id"] not in cards]
            if not left:
                break
            done = [{**s, **cards[s["id"]]} for s in to_name if s["id"] in cards]
            request = {"devices": devices, "to_name": [see(s) for s in left],
                       "already_named": [{**see(s), "name": s["name"], "importance": s.get("importance")}
                                         for s in named + done]}
            text = _ask(model, prompt, request)
            cards |= parse_cards(text, {s["id"] for s in left},
                                 {s["name"] for s in named} | {c["name"] for c in cards.values()})
    except Exception:  # the namer must never stop the learners
        log.exception("namer failed; %ss without a card keep their current names", thing.label)
    stored = []
    for s in to_name:
        card = cards.get(s["id"])
        if card is None:
            continue
        # read again: a person may have named it while the LLM was answering
        row = db.execute(f"SELECT data FROM {thing.table} WHERE id = ?", (s["id"],)).fetchone()
        data = json.loads(row[0]) if row else None
        if data is None or data.get("named_by") == "person":
            continue
        if "quiet_expects" in card:
            mine = {e.get("device") for e in data.get("expects") or [] if isinstance(e, dict)}
            card = card | {"quiet_expects": [q for q in card["quiet_expects"] if q in mine]}
        data.update(card, named_by="llm")
        db.execute(f"UPDATE {thing.table} SET data = ? WHERE id = ?", (json.dumps(data), s["id"]))
        stored.append({"id": s["id"], **card})
    db.commit()
    missing = len(to_name) - len(stored)
    log.info("namer: named %d %s(s)%s", len(stored), thing.label, f", {missing} left unnamed" if missing else "",
             extra={"data": {"cards": stored}})
    return stored


def _ask(model, prompt: str, request: dict) -> str:
    """The LLM's reply, through the reply cache (source/llm_cache.py) when the model has a name."""
    def call() -> str:
        return _content(model.invoke([SystemMessage(prompt), HumanMessage(json.dumps(request))]))

    name = llm_cache.model_name(model)
    if name is None:
        return call()
    return llm_cache.cached("namer", {"model": name, "prompt": prompt, "request": request}, call)


def name_sensors(db: sqlite3.Connection, model, *, again: bool = False) -> list[dict]:
    """Names the patterns (the learners' sensors)."""
    return _name(db, model, PATTERNS, again)


def name_habits(db: sqlite3.Connection, model, *, again: bool = False) -> list[dict]:
    """Names the habits (source/learn/grouping.py), after their patterns: a habit's steps show their names."""
    return _name(db, model, HABITS, again)


def name_occasions(db: sqlite3.Connection, model, *, again: bool = False) -> list[dict]:
    """Names the occasions (source/learn/occasions.py), after the habits they move."""
    return _name(db, model, OCCASIONS, again)


def name_all(db: sqlite3.Connection, model, *, again: bool = False) -> list[dict]:
    """Patterns, then habits, then occasions: each later call reads the names the earlier ones gave."""
    return name_sensors(db, model, again=again) + name_habits(db, model, again=again) \
        + name_occasions(db, model, again=again)


def saved_cards(db: sqlite3.Connection) -> dict[str, dict]:
    """The LLM's cards on this history, to reuse on another run of the same home (see reuse_cards)."""
    out = {}
    for thing in THINGS:
        if not _table(db, thing.table):
            continue
        for s in _rows(db, thing.table):
            if s.get("named_by") == "llm" and s.get("name"):
                out[thing.key(db, s)] = {k: s.get(k) for k in CARD_FIELDS}
    return out


def reuse_cards(db: sqlite3.Connection, cards: dict[str, dict]) -> int:
    """Puts saved cards back on unnamed patterns, habits and occasions with the same key, so each is named once
    and keeps its name. A changed one has another key: it is named again. Returns how many were reused."""
    n = 0
    for thing in THINGS:
        if not _table(db, thing.table):
            continue
        for s in _rows(db, thing.table):
            card = cards.get(thing.key(db, s))
            if card is None or s.get("named_by") is not None:
                continue
            s.update(card, named_by="llm")
            db.execute(f"UPDATE {thing.table} SET data = ? WHERE id = ?", (json.dumps(s), s["id"]))
            n += 1
    db.commit()
    return n


def main(argv: list[str] | None = None) -> int:
    from ..agent import make_model
    from ..config import load_settings

    ap = argparse.ArgumentParser(prog="source.learn.namer",
                                 description="Name the learned patterns, habits and occasions with the LLM.")
    ap.add_argument("--db", type=Path, required=True, help="SQLite file with the learners' tables")
    ap.add_argument("--again", action="store_true", help="also rename what the LLM named before")
    args = ap.parse_args(argv)
    settings = load_settings()
    if not settings.openrouter_api_key:
        log.error("OPENROUTER_API_KEY is not set")
        return 1
    db = sqlite3.connect(args.db)
    try:
        cards = name_all(db, make_model(settings.agent_model, settings.openrouter_api_key), again=args.again)
    finally:
        db.close()
    for c in cards:
        same = f" (same as {c['same_as']})" if c["same_as"] else ""
        print(f"{c['id']:<11} [{c['importance']}] {c['name']}{same}\n            {c['explanation']}\n"
              f"            means: {c['means']}\n            if missing: {c['if_missing']}")
    return 0


if __name__ == "__main__":
    from ..logs import setup_logging

    setup_logging(None)
    raise SystemExit(main())
