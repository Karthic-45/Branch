"""/api/mcp/tools reports the real MCP server's tool list -- no hardcoded catalogue."""

import asyncio

from fastapi.testclient import TestClient

from bmem.api import app
from bmem.mcp_server import mcp


def test_mcp_tools_matches_server_registry():
    expected = {t.name for t in asyncio.run(mcp.list_tools())}
    body = TestClient(app).get("/api/mcp/tools").json()

    assert {t["name"] for t in body["tools"]} == expected
    assert body["total"] == len(expected)
    assert body["read_only"] + body["may_write"] == body["total"]
    for t in body["tools"]:
        assert set(t) == {"name", "description", "read_only", "destructive"}
