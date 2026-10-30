"""Focused tests for main.py tool-failure detection used by the learning system."""

from __future__ import annotations

import types

import pytest

import main as jarvis_main
from memory import lessons


@pytest.fixture()
def lesson_store(tmp_path, monkeypatch):
    path = tmp_path / "lessons.json"
    monkeypatch.setattr(lessons, "LESSONS_PATH", path)
    return path


def test_is_tool_failure_result_recognises_known_failure_shapes():
    assert jarvis_main._is_tool_failure_result("Tool 'open_app' failed: boom")
    assert jarvis_main._is_tool_failure_result("Sir, the 'x' plugin failed: boom")
    assert jarvis_main._is_tool_failure_result("Unknown tool: nope")
    assert jarvis_main._is_tool_failure_result("Action 'x' is not available.")
    assert jarvis_main._is_tool_failure_result("MCP call failed: timeout")
    assert jarvis_main._is_tool_failure_result("MCP tool reported an error: bad")


def test_is_tool_failure_result_ignores_normal_results():
    assert not jarvis_main._is_tool_failure_result("Done.")
    assert not jarvis_main._is_tool_failure_result("Opened notepad.")
    assert not jarvis_main._is_tool_failure_result(None)
    assert not jarvis_main._is_tool_failure_result(42)
    assert not jarvis_main._is_tool_failure_result("  error happened  ")


def test_lesson_note_includes_prior_failure(lesson_store):
    lessons.record_tool_failure("open_app", "File not found", args={"app": "missing"})
    item = lessons.record_tool_failure("open_app", "File not found again", args={"app": "missing"})

    note = jarvis_main._lesson_note("open_app", item)

    assert "INTERNAL LEARNING NOTE" in note
    assert "do not retry" in note.lower() or "failed" in note.lower()


def test_tool_learning_note_persists_failure(lesson_store):
    holder = types.SimpleNamespace(_latest_user_request="open calculator")
    logs: list[str] = []

    note = jarvis_main.JarvisLive._tool_learning_note(
        holder, "open_app", "boom", {"app": "missing"}
    )

    assert "INTERNAL LEARNING NOTE" in note
    assert lessons.load_lessons()
    assert lessons.load_lessons()[0]["scope"] == "open_app"


def test_observe_user_turn_stores_correction(lesson_store):
    holder = types.SimpleNamespace(
        _last_user_request_text="Открой блокнот",
        _last_assistant_reply="Открыл блокнот.",
        ui=types.SimpleNamespace(write_log=lambda message: None),
    )
    messages: list[str] = []
    holder.ui.write_log = messages.append

    jarvis_main.JarvisLive._observe_user_turn(
        holder, "Не то, я просил калькулятор"
    )

    items = lessons.load_lessons()
    assert items and items[0]["kind"] == "correction"
    assert "калькулятор" in items[0]["fix"]