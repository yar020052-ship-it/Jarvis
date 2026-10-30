"""Edge case tests for behavioral lessons."""

from __future__ import annotations

import json
import types

import pytest

import main as jarvis_main
from memory import lessons


@pytest.fixture()
def lesson_store(tmp_path, monkeypatch):
    path = tmp_path / "lessons.json"
    monkeypatch.setattr(lessons, "LESSONS_PATH", path)
    return path


def test_forget_by_id(lesson_store):
    item = lessons.remember_rule("Never close the browser")
    assert item is not None
    removed = lessons.forget(item["id"])
    assert removed == 1
    assert len(lessons.load_lessons()) == 0


def test_forget_by_query(lesson_store):
    lessons.remember_rule("Never close the browser")
    removed = lessons.forget("Never")
    assert removed == 1


def test_clear_auto_keeps_owner_rules(lesson_store):
    lessons.remember_rule("Owner rule persists")
    lessons.record_tool_failure("open_app", "boom")
    removed = lessons.clear_auto()
    assert removed == 1
    items = lessons.load_lessons()
    assert len(items) == 1
    assert items[0]["kind"] == "explicit"


def test_clear_auto_keeps_owner_rules_and_corrections(lesson_store):
    lessons.remember_rule("Rule 1")
    lessons.record_user_correction(
        "Ne too, I wanted calculator",
        previous_request="Open blocknote",
        previous_reply="Opened blocknote.",
    )
    removed = lessons.clear_auto()
    # clear_auto keeps owner rules (explicit) and owner corrections.
    assert removed == 0
    items = lessons.load_lessons()
    assert len(items) == 2
    kinds = {item["kind"] for item in items}
    assert kinds == {"explicit", "correction"}


def test_stats_reflects_kinds(lesson_store):
    lessons.remember_rule("Explicit rule")
    lessons.record_tool_failure("web_search", "timeout")
    lessons.record_tool_failure("web_search", "timeout again")
    summary = lessons.stats()
    assert summary["total"] == 3
    assert summary["open"] == 2
    assert summary["resolved"] == 0
    assert summary["owner"] == 1
    assert summary["kinds"]["explicit"] == 1
    assert summary["kinds"]["tool_failure"] == 2


def test_transient_failure_not_in_prompt_until_repeated(lesson_store):
    lessons.record_tool_failure("web_search", "request timed out")
    assert lessons.format_lessons_for_prompt() == ""

    lessons.record_tool_failure("web_search", "request timed out")
    prompt = lessons.format_lessons_for_prompt()
    assert "web_search" in prompt
    assert "Transient failure" in prompt


def test_lesson_note_with_prior(lesson_store):
    lessons.record_tool_failure("open_app", "first error", args={"app": "missing"})
    item = lessons.record_tool_failure("open_app", "second error", args={"app": "missing"})

    note = jarvis_main._lesson_note("open_app", item)
    assert "INTERNAL LEARNING NOTE" in note