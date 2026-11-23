"""The studio waits JEEN_MCP_READ_TIMEOUT_SECONDS for Jeen's metadata MCP."""

import json
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

from app.config import settings
from app.routers import agent


class FakeJeen:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments, read_timeout_seconds):
        self.calls.append((name, read_timeout_seconds))
        text = json.dumps(
            [{"name": "superhero-alignment", "schema_name": "agency__bird"}]
            if name == "list_tables_rich"
            else {"connections": []}
        )
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_jeen_calls_use_the_configured_timeout(client, monkeypatch):
    jeen = FakeJeen()

    @asynccontextmanager
    async def fake_client():
        yield jeen

    monkeypatch.setattr(agent, "_get_jeen_mcp_client", fake_client)
    monkeypatch.setattr(settings, "JEEN_MCP_READ_TIMEOUT_SECONDS", 420.0)

    tables = client.get("/api/agent/connections/7/tables").json()
    client.get("/api/agent/connections")

    assert [t["name"] for t in tables] == ["superhero-alignment"]
    assert jeen.calls == [
        ("list_tables_rich", timedelta(seconds=420)),
        ("list_connections", timedelta(seconds=420)),
    ]
