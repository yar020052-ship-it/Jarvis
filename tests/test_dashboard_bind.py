"""Bind и выбор портов дашборда: локально по умолчанию, LAN — только явно."""

from __future__ import annotations

import socket

import pytest

from dashboard import server


def test_default_bind_is_loopback(monkeypatch):
    monkeypatch.delenv("JARVIS_DASHBOARD_REMOTE", raising=False)
    monkeypatch.setattr(server, "load_config", lambda name: {})
    assert server._resolve_bind_host() == "127.0.0.1"


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_env_opt_in_enables_lan(monkeypatch, value):
    monkeypatch.setenv("JARVIS_DASHBOARD_REMOTE", value)
    monkeypatch.setattr(server, "load_config", lambda name: {})
    assert server._resolve_bind_host() == "0.0.0.0"


def test_config_opt_in_enables_lan(monkeypatch):
    monkeypatch.delenv("JARVIS_DASHBOARD_REMOTE", raising=False)
    monkeypatch.setattr(
        server, "load_config", lambda name: {"dashboard_remote": True}
    )
    assert server._resolve_bind_host() == "0.0.0.0"


def test_config_error_keeps_loopback(monkeypatch):
    monkeypatch.delenv("JARVIS_DASHBOARD_REMOTE", raising=False)

    def broken(name):
        raise OSError("config is unreadable")

    monkeypatch.setattr(server, "load_config", broken)
    assert server._resolve_bind_host() == "127.0.0.1"


def test_busy_port_is_not_free():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        assert server._port_free(port, "127.0.0.1") is False
    finally:
        sock.close()


def test_choose_ports_returns_the_first_free_pair():
    base = None
    for candidate in range(20000, 20500):
        if server._port_free(candidate, "127.0.0.1") and server._port_free(
            candidate + 1, "127.0.0.1"
        ):
            base = candidate
            break
    if base is None:
        pytest.skip("no free consecutive ports in range")

    assert server._choose_ports("127.0.0.1", start=base) == (base, base + 1)


def test_choose_ports_skips_busy_pair():
    first = socket.socket()
    second = socket.socket()
    try:
        first.bind(("127.0.0.1", 0))
        busy = first.getsockname()[1]
        try:
            second.bind(("127.0.0.1", busy + 1))
        except OSError:
            pytest.skip("could not reserve a consecutive pair")
        first.listen(1)
        second.listen(1)

        port, alias = server._choose_ports(
            "127.0.0.1", start=busy, tries=10
        )
        assert (port, alias) != (busy, busy + 1)
        assert alias == port + 1
    finally:
        first.close()
        second.close()


@pytest.mark.skipif(not server._DEPS_OK, reason="fastapi/uvicorn not installed")
def test_dashboard_server_defaults_to_local_pair(monkeypatch):
    monkeypatch.delenv("JARVIS_DASHBOARD_REMOTE", raising=False)
    monkeypatch.setattr(server, "load_config", lambda name: {})

    dashboard = server.DashboardServer()

    assert dashboard._host == "127.0.0.1"
    assert dashboard._remote is False
    assert dashboard._ip == "127.0.0.1"
    assert dashboard.alias_port == dashboard.port + 1
    assert f":{dashboard.port}" in dashboard.get_url()
