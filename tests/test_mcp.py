"""Exercise the actual bundled MCP v2 protocol over stdio, not a mocked Context."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

try:
    from mcp import ClientSession, StdioServerParameters, stdio_client
except ImportError:
    ClientSession = None


@unittest.skipIf(ClientSession is None, "MCP SDK is provided by Locus (install mcp==2.0.0 for this test)")
class MCPTests(unittest.TestCase):
    def test_stdio_native_context_and_single_json_text_response(self):
        async def exercise(root):
            parameters = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).parents[1] / "server.py")], env={**os.environ, "LOCUS_PLUGIN_DATA": root})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    listed = await client.list_tools()
                    self.assertEqual([t.name for t in listed.tools], ["social_studio"])
                    self.assertNotIn("workspace", listed.tools[0].input_schema["properties"])
                    missing = await client.call_tool("social_studio", {"operation": "state", "payload": {}})
                    self.assertTrue(missing.is_error)
                    meta = {"com.locus/panel": {"version": 1, "workspace": str(Path(root) / "project"), "pluginId": "locus-openpost/social-studio", "panelId": "social-studio", "digest": "abc123"}}
                    state = await client.call_tool("social_studio", {"operation": "state", "payload": {}}, meta=meta)
                    self.assertFalse(state.is_error, state.content)
                    self.assertIsNone(state.structured_content)
                    self.assertEqual(len(state.content), 1)
                    self.assertEqual(json.loads(state.content[0].text)["total"], 0)
                    created = await client.call_tool("social_studio", {"operation": "draft_create", "payload": {}}, meta=meta)
                    self.assertFalse(created.is_error, created.content)
                    draft = json.loads(created.content[0].text)["draft"]
                    updated = await client.call_tool("social_studio", {"operation": "draft_update", "payload": {"id": draft["id"], "field": "text", "value": "Hello from MCP"}}, meta=meta)
                    self.assertEqual(json.loads(updated.content[0].text)["draft"]["text"], "Hello from MCP")
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(exercise(root))


if __name__ == "__main__":
    unittest.main()
