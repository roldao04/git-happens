"""The agent step. v1 is a stub that only reports what it would have been given.

This is where a LangGraph graph plugs in later (wake → reason → tools → verify), with the same
contract: context dict in, {notes, trigger_updates, actions} out. Actions are never executed
in v1.
"""
import logging

log = logging.getLogger("source.agent")


class StubAgent:
    async def run(self, ctx: dict) -> dict:
        why = ", ".join(r["trigger_id"] for r in ctx["reasons"])
        log.info("wake   (%s) %d new events since last run",
                 why, len(ctx["events_since_last_run"]),
                 extra={"data": {"reasons": ctx["reasons"], "events": ctx["events_since_last_run"]}})
        for line in ctx["events_since_last_run"][-10:]:
            log.debug("  %s", line)
        return {"notes": "stub", "trigger_updates": [], "actions": []}
