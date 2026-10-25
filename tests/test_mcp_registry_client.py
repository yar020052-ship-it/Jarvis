"""Tests for MCP registry HTTP client construction and header resolution."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core import mcp_registry


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.headers = kwargs.get("headers", {})
        self.timeout = kwargs.get("timeout")
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


class _FakeClient:
    def __init__(self, transport):
        self.transport = transport

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text('{"servers": []}', encoding="utf-8")

    monkeypatch.setattr(mcp_registry.MCPRegistry, "_discover", lambda self: None)
    reg = mcp_registry.MCPRegistry(config_path, logger=lambda _line: None)
    reg._ready.set()
    return reg


def test_http_headers_resolve_env(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "secret-token")
    entry = {"headers": {"Authorization": "${MCP_TOKEN}", "X-Test": "literal-not-allowed"}}

    # Literal values are rejected; only env references are accepted.
    with pytest.raises(ValueError):
        mcp_registry.MCPRegistry._http_headers(entry)

    valid_entry = {"headers": {"Authorization": "${MCP_TOKEN}"}}
    assert mcp_registry.MCPRegistry._http_headers(valid_entry) == {
        "Authorization": "secret-token"
    }


def test_client_stdio_yields_client(registry, monkeypatch):
    async def run():
        import types

        fake_mcp = types.ModuleType("mcp")
        fake_mcp.Client = _FakeClient
        monkeypatch.setitem(__import__("sys").modules, "mcp", fake_mcp)

        registry._server_params = lambda entry: "params"
        async with registry._client({"transport": "stdio", "command": "echo"}) as client:
            assert isinstance(client, _FakeClient)

    asyncio.run(run())


def test_client_http_uses_streamable_client(registry, monkeypatch):
    async def run():
        import sys
        import types

        fake_httpx = types.ModuleType("httpx")
        fake_httpx.AsyncClient = _FakeAsyncClient
        fake_httpx.Timeout = lambda **kwargs: kwargs
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

        calls = {}

        def fake_streamable(url, http_client=None):
            calls["url"] = url
            calls["client"] = http_client
            return "transport"

        fake_streamable_module = types.ModuleType("mcp.client.streamable_http")
        fake_streamable_module.streamable_http_client = fake_streamable
        monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", fake_streamable_module)

        # Ensure parent package exists for import.
        if "mcp" not in sys.modules:
            monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
        sys.modules["mcp"].Client = _FakeClient

        monkeypatch.setenv("MCP_TOKEN", "secret-token")
        entry = {
            "transport": "http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "${MCP_TOKEN}"},
        }

        async with registry._client(entry) as client:
            assert isinstance(client, _FakeClient)
            assert client.transport == "transport"
            assert calls["url"] == "https://example.test/mcp"
            assert calls["client"].headers == {"Authorization": "secret-token"}

    asyncio.run(run())
