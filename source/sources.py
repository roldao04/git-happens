"""Where snapshots come from. Every source has the same `fetch() -> (ts_ms, home_json)`.

- McpSource: the live Connected Home MCP. It starts the same `mcp-remote` proxy Claude Code
  uses, so it reuses that OAuth sign-in (cached in ~/.mcp-auth) and avoids the Cloudflare block
  a plain Python HTTP client hit.
- ReplaySource: a JSONL recording written by the loop (or a hand-made scenario), for testing
  without the kit and, later, for the simulator.
"""
import json
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Protocol

MCP_URL = "https://cognitive-home.futurelabsinnovations.com"


class SourceError(Exception):
    pass


class Source(Protocol):
    realtime: bool  # False = replay as fast as possible, using the recorded timestamps

    async def fetch(self) -> tuple[int, dict]: ...
    async def close(self) -> None: ...


class McpSource:
    realtime = True

    def __init__(self, api_key: str, proxy_log: Path | None = None):
        self.api_key = api_key
        self.proxy_log = proxy_log  # mcp-remote's stderr (very chatty); None = discard
        self._stack: AsyncExitStack | None = None
        self._session = None

    def _server_params(self):
        from mcp import StdioServerParameters

        # Keep these args identical to the Claude Code config: mcp-remote keys its token
        # cache on them, so any difference means a fresh browser sign-in.
        return StdioServerParameters(
            command="npx",
            args=[
                "-y", "mcp-remote@0.16.6", MCP_URL, "5435",
                "--host", "mcp-redirect.futurelabsinnovations.com",
                "--callback-path", "/callback",
                "--transport", "http-only",
                "--static-oauth-client-info", '{"client_id":"mcp-client"}',
                "--header", "X-Client-Type:core",
                "--header", f"X-API-Key:{self.api_key}",
            ],
        )

    async def _connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        errlog = self._stack.enter_context(open(self.proxy_log or os.devnull, "a"))
        read, write = await self._stack.enter_async_context(
            stdio_client(self._server_params(), errlog=errlog))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def fetch(self) -> tuple[int, dict]:
        try:
            if self._session is None:
                await self._connect()
            result = await self._session.call_tool("get_home", {})
        except Exception as exc:  # connection dropped: reconnect on the next fetch
            await self.close()
            raise SourceError(f"MCP call failed: {exc!r}") from exc
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
