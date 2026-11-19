"""Where snapshots come from. Every source has the same `fetch() -> (ts_ms, home_json)`.

- McpSource: the live Connected Home MCP, over HTTP with the same sign-in as Claude Code
  (tools/connected_home_auth.py: one browser login, then the token refreshes itself). It sets its
  own User-Agent: the default Python one is what Cloudflare blocked.
- ReplaySource: a JSONL recording written by the loop (or a hand-made scenario), for testing
  without the kit and, later, for the simulator.
"""
import asyncio
import importlib.util
import json
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Protocol

MCP_URL = "https://cognitive-home.futurelabsinnovations.com"
AUTH_TOOL = Path(__file__).resolve().parent.parent / "tools" / "connected_home_auth.py"
LOGIN = "python3 tools/connected_home_auth.py login"
USER_AGENT = "alfred-loop/1.0"
CONNECT_TIMEOUT_S = 30
CALL_TIMEOUT_S = 30


class SourceError(Exception):
    pass


class Source(Protocol):
    realtime: bool  # False = replay as fast as possible, using the recorded timestamps

    async def fetch(self) -> tuple[int, dict]: ...
    async def close(self) -> None: ...


def _auth_tool():
    """tools/connected_home_auth.py, the sign-in Claude Code uses (standard library only)."""
    spec = importlib.util.spec_from_file_location("connected_home_auth", AUTH_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kit_auth(tool):
    """An httpx.Auth that puts a fresh bearer token on every request, so a loop running for days stays signed in."""
    import httpx

    class KitAuth(httpx.Auth):
        async def async_auth_flow(self, request):
            request.headers["Authorization"] = f"Bearer {await asyncio.to_thread(tool.bearer)}"
            yield request

    return KitAuth()


class McpSource:
    realtime = True

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._stack: AsyncExitStack | None = None
        self._session = None

    async def _connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        tool = _auth_tool()
        try:
            await asyncio.to_thread(tool.bearer)  # signed in? (and refreshed if it was about to expire)
        except tool.NotLoggedIn as e:
            raise SourceError(f"not signed in to the kit ({e}). Run {LOGIN} once, in the browser.") from None
        self._stack = AsyncExitStack()
        read, write, _ = await self._stack.enter_async_context(streamablehttp_client(
            MCP_URL, headers={"X-Client-Type": "core", "X-API-Key": self.api_key, "User-Agent": USER_AGENT},
            auth=_kit_auth(tool)))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        try:
            await asyncio.wait_for(self._session.initialize(), CONNECT_TIMEOUT_S)
        except TimeoutError:
            raise SourceError(f"the kit didn't answer within {CONNECT_TIMEOUT_S} s") from None

    async def session(self):
        """The open MCP ClientSession (connecting first if needed), shared with the agent."""
        if self._session is None:
            try:
                await self._connect()
            except SourceError:
                await self.close()
                raise
            except Exception as exc:
                await self.close()
                raise SourceError(f"MCP connect failed: {exc!r}") from exc
        return self._session

    async def fetch(self) -> tuple[int, dict]:
        session = await self.session()
        try:
            result = await asyncio.wait_for(session.call_tool("get_home", {}), CALL_TIMEOUT_S)
        except Exception as exc:  # connection dropped (or no answer): reconnect on the next fetch
            await self.close()
            why = f"get_home didn't answer within {CALL_TIMEOUT_S} s" if isinstance(exc, TimeoutError) else f"MCP call failed: {exc!r}"
            raise SourceError(why) from exc
        text = "".join(getattr(c, "text", "") for c in result.content)
        if getattr(result, "is_error", None) or getattr(result, "isError", False):
            raise SourceError(f"get_home returned an error: {text[:300]}")
        try:
            return int(time.time() * 1000), json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError(f"get_home returned non-JSON: {text[:300]}") from exc

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
        self._stack, self._session = None, None


class ReplaySource:
    """Reads lines of {"ts": <ms>, "home": {...}}. Raises StopAsyncIteration at the end."""

    realtime = False

    def __init__(self, path: Path):
        self._lines = iter(Path(path).read_text().splitlines())

    async def fetch(self) -> tuple[int, dict]:
        for line in self._lines:
            if line.strip():
                row = json.loads(line)
                return int(row["ts"]), row["home"]
        raise StopAsyncIteration

    async def close(self) -> None:
        pass
