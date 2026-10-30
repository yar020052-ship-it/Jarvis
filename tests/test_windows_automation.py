"""Tests for Windows automation helpers.

These tests never change the real system state: every subprocess boundary is
monkeypatched. They verify argument construction and the safety checks around
operations that need administrator rights.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from windows_automation import (  # noqa: E402
    OperationResult,
    cancel_shutdown,
    is_admin,
    open_settings,
    set_wifi,
    shutdown,
    wifi_enabled,
)


def test_is_admin_returns_bool():
    assert isinstance(is_admin(), bool)


def test_wifi_enabled_parses_netsh_output(monkeypatch):
    import windows_automation as wa

    monkeypatch.setattr(
        wa,
        "_run_command",
        lambda *a, **k: OperationResult(True, "ok", {"stdout": "Admin State    State\nEnabled"}),
    )
    assert wifi_enabled() is True

    monkeypatch.setattr(
        wa,
        "_run_command",
        lambda *a, **k: OperationResult(True, "ok", {"stdout": "Admin State    State\nDisabled"}),
    )
    assert wifi_enabled() is False


def test_set_wifi_requires_admin(monkeypatch):
    import windows_automation as wa

    monkeypatch.setattr(wa, "is_admin", lambda: False)
    result = set_wifi(False)

    assert result.success is False
    assert result.details["admin_required"] is True


def test_set_wifi_calls_netsh(monkeypatch):
    import windows_automation as wa

    captured = {}

    def fake_run(args, timeout=25):
        captured["args"] = args
        return OperationResult(True, "ok")

    monkeypatch.setattr(wa, "is_admin", lambda: True)
    monkeypatch.setattr(wa, "_run_command", fake_run)

    result = set_wifi(True)

    assert result.success is True
    assert captured["args"][-1] == "admin=enable"


def test_shutdown_delay_is_clamped(monkeypatch):
    import windows_automation as wa

    captured = {}

    def fake_run(args, timeout=25):
        captured["args"] = args
        return OperationResult(True, "ok")

    monkeypatch.setattr(wa, "_run_command", fake_run)
    shutdown(delay_seconds=99999)

    assert captured["args"][-1] == "600"


def test_cancel_shutdown(monkeypatch):
    import windows_automation as wa

    captured = {}

    def fake_run(args, timeout=25):
        captured["args"] = args
        return OperationResult(True, "ok")

    monkeypatch.setattr(wa, "_run_command", fake_run)
    result = cancel_shutdown()

    assert result.success is True
    assert captured["args"] == ["shutdown", "/a"]


def test_open_settings_rejects_unknown_page():
    result = open_settings("definitely-not-a-page")
    assert result.success is False
    assert "Неизвестная страница" in result.message


def test_open_settings_accepts_known_page(monkeypatch):
    import windows_automation as wa

    opened = []
    monkeypatch.setattr(wa.os, "startfile", lambda uri: opened.append(uri), raising=False)

    result = open_settings("wifi")

    assert result.success is True
    assert opened == ["ms-settings:network/wifi"]