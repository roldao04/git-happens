"""The live source's sign-in (source/sources.py with tools/connected_home_auth.py): the token Claude Code uses,
refreshed when old, on every request; a plain reason when not signed in or when the kit doesn't answer.
No network: the token endpoint and the MCP session are stand-ins.

  uv run python -m unittest tests.test_sources -v
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from source import sources


def tool_with(token_file: Path):
    tool = sources._auth_tool()
    tool.TOKEN_FILE = str(token_file)
    return tool


class SignInTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "tokens.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_old_token_is_refreshed_and_a_fresh_one_is_kept(self):
        tool = tool_with(self.file)
        self.file.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "obtained_at": 0, "expires_in": 300}))
        calls = []

        def post_token(data):
            calls.append(data)
            tok = {"access_token": "new", "refresh_token": "r2", "obtained_at": 10**10, "expires_in": 300}
            self.file.write_text(json.dumps(tok))
            return tok
        tool.post_token = post_token
        self.assertEqual(tool.bearer(), "new")
        self.assertEqual((calls[0]["grant_type"], calls[0]["refresh_token"]), ("refresh_token", "r1"))
        self.assertEqual(tool.bearer(), "new")  # still fresh: no second refresh
        self.assertEqual(len(calls), 1)

    def test_every_request_carries_the_current_token(self):
        tool = tool_with(self.file)
        tool.bearer = lambda: "abc"

        async def go():
            flow = sources._kit_auth(tool).async_auth_flow(httpx.Request("POST", sources.MCP_URL))
            return await flow.__anext__()
        self.assertEqual(asyncio.run(go()).headers["Authorization"], "Bearer abc")

    def test_not_signed_in_says_how_to_sign_in(self):
        tool = tool_with(self.file)  # no token file
        with mock.patch.object(sources, "_auth_tool", return_value=tool):
            with self.assertRaises(sources.SourceError) as e:
                asyncio.run(sources.McpSource("key").fetch())
        self.assertIn("connected_home_auth.py login", str(e.exception))

    def test_a_kit_that_doesnt_answer_is_a_plain_error_not_a_hang(self):
        tool = tool_with(self.file)
        tool.bearer = lambda: "abc"

        async def never(self):
            await asyncio.sleep(10)
        with mock.patch.object(sources, "_auth_tool", return_value=tool), \
                mock.patch.object(sources, "CONNECT_TIMEOUT_S", 0.05), \
                mock.patch("mcp.ClientSession.initialize", never):
            with self.assertRaises(sources.SourceError) as e:
                asyncio.run(sources.McpSource("key").fetch())
        self.assertIn("didn't answer", str(e.exception))


if __name__ == "__main__":
    unittest.main()
