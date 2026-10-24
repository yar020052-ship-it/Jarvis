"""Watchdog: ожидаемые ошибки не должны превращаться в инциденты и починки."""

from __future__ import annotations

import pytest

from autonomy import watchdog


class _Incidents:
    def __init__(self):
        self.added = []

    def add(self, *args, **kwargs):
        self.added.append((args, kwargs))
        return {"id": "inc-1"}


class _Journal:
    def __init__(self):
        self.notes = []

    def add(self, kind, text):
        self.notes.append((kind, text))


class _Repair:
    def __init__(self):
        self.repaired = []

    def repair_incident(self, incident):
        self.repaired.append(incident)
        return {}


def _block(path: str, last_line: str) -> str:
    return (
        "Traceback (most recent call last):\n"
        f'  File "{path}", line 42, in handler\n'
        "    raise RuntimeError(...)\n"
        f"{last_line}"
    )


def _handle(block: str, seen=None, auto_repair=True, journal=None):
    incidents = _Incidents()
    journal = _Journal() if journal is None else journal
    repair = _Repair()
    result = watchdog._handle_traceback(
        block,
        {"auto_repair": auto_repair},
        set() if seen is None else seen,
        incidents,
        journal,
        repair,
    )
    return result, incidents, journal, repair


ANDROID = r"C:\JARVIS\actions\android_control.py"


@pytest.mark.parametrize(
    "last_line",
    [
        "RuntimeError: no API key configured",
        "RuntimeError: Gemini API key is not configured in the JARVIS user settings.",
        "RuntimeError: API key not valid. Please pass a valid API key.",
        "RuntimeError: Подключено несколько устройств (A, B). Укажите device_id.",
        "RuntimeError: Нет Android-устройства в режиме adb. Подключите USB.",
        "RuntimeError: ADB не найден. Установите Android SDK Platform-Tools.",
        "RuntimeError: device offline",
        "RuntimeError: user denied",
        "CommandError: Действие отменено пользователем (или не подтверждено)",
        "RuntimeError: [Dashboard] fastapi/uvicorn not installed",
        "ModuleNotFoundError: No module named 'fastapi'",
    ],
)
def test_expected_errors_are_not_incidents(last_line):
    result, incidents, journal, repair = _handle(_block(ANDROID, last_line))

    assert result == "expected"
    assert incidents.added == []
    assert repair.repaired == []
    assert len(journal.notes) == 1
    assert journal.notes[0][0] == "note"


def test_expected_error_is_noted_only_once():
    seen = set()
    journal = _Journal()
    block = _block(ANDROID, "RuntimeError: ADB не найден.")

    _handle(block, seen=seen, journal=journal)
    result, incidents, journal, repair = _handle(
        block, seen=seen, journal=journal
    )

    assert result == "expected"
    assert incidents.added == []
    assert repair.repaired == []
    assert len(journal.notes) == 1


def test_genuine_error_creates_incident_and_repairs(monkeypatch):
    monkeypatch.setattr(watchdog, "_repairs_today", lambda: 0)

    result, incidents, journal, repair = _handle(
        _block(ANDROID, "KeyError: 'serial'")
    )

    assert result == "repaired"
    assert len(incidents.added) == 1
    assert len(repair.repaired) == 1
    assert journal.notes == []


def test_genuine_error_with_repair_off_is_only_noted():
    result, incidents, journal, repair = _handle(
        _block(ANDROID, "AttributeError: 'NoneType' object has no attribute 'tap'"),
        auto_repair=False,
    )

    assert result == "noted"
    assert len(incidents.added) == 1
    assert repair.repaired == []
    assert len(journal.notes) == 1
    assert journal.notes[0][0] == "note"


def test_daily_limit_blocks_repair(monkeypatch):
    monkeypatch.setattr(watchdog, "_repairs_today", lambda: watchdog.MAX_PER_DAY)

    result, incidents, journal, repair = _handle(
        _block(ANDROID, "ValueError: invalid literal for int()")
    )

    assert result == "noted"
    assert len(incidents.added) == 1
    assert repair.repaired == []
    assert len(journal.notes) == 1


def test_error_outside_own_code_is_skipped():
    result, incidents, journal, repair = _handle(
        _block(r"C:\Python313\lib\threading.py", "KeyError: 'x'")
    )

    assert result == "skipped"
    assert incidents.added == []
    assert repair.repaired == []
    assert journal.notes == []


def test_classifier_does_not_swallow_generic_errors():
    assert watchdog.expected_failure_reason("KeyError: 'serial'") is None
    assert watchdog.expected_failure_reason("AttributeError: object is None") is None
    assert watchdog.expected_failure_reason("") is None
