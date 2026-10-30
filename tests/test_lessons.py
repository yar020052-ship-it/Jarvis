"""Tests for the behavioural lesson store."""

from __future__ import annotations

import json

import pytest

from memory import lessons


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "lessons.json"
    monkeypatch.setattr(lessons, "LESSONS_PATH", path)
    return path


def test_explicit_rule_is_stored_and_formatted(store):
    item = lessons.remember_rule(
        "Never close the browser without asking",
        trigger="when the owner opens a new tab",
    )

    assert item is not None
    assert item["kind"] == "explicit"
    assert item["source"] == "owner"
    assert store.exists()

    prompt = lessons.format_lessons_for_prompt()
    assert "Owner rule: Never close the browser without asking" in prompt
    assert "LESSONS FROM YOUR OWN MISTAKES" in prompt


def test_repeated_rule_is_deduplicated(store):
    first = lessons.remember_rule("Always ask before deleting a file")
    second = lessons.remember_rule("Always ask before deleting a file")

    assert first["id"] == second["id"]
    items = lessons.load_lessons()
    assert len(items) == 1
    assert items[0]["hits"] == 2
    assert items[0]["confidence"] > first["confidence"] - 0.001


def test_tool_failure_redacts_arguments_and_success_teaches_fix(store):
    failed = lessons.record_tool_failure(
        "open_app",
        "File not found: C:\\Users\\Owner\\secret.txt",
        args={"app": "missing", "password": "hunter2"},
    )

    assert failed is not None
    assert "hunter2" not in json.dumps(lessons.load_lessons(), ensure_ascii=False)
    assert "app=missing" in failed["args_hint"]

    prompt_after_failure = lessons.format_lessons_for_prompt()
    assert "previously failed" in prompt_after_failure
    assert "hunter2" not in prompt_after_failure

    resolved = lessons.record_tool_success("open_app", args={"app": "notepad"})
    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert "notepad" in resolved["fix"]

    prompt_after_success = lessons.format_lessons_for_prompt()
    assert "What works" in prompt_after_success
    assert "notepad" in prompt_after_success


def test_transient_failure_stays_out_of_prompt_until_repeated(store):
    lessons.record_tool_failure("web_search", "request timed out")
    assert lessons.format_lessons_for_prompt() == ""

    lessons.record_tool_failure("web_search", "request timed out")
    prompt = lessons.format_lessons_for_prompt()
    assert "web_search" in prompt
    assert "Transient failure" in prompt


@pytest.mark.parametrize(
    "text",
    [
        "Не то, я просил открыть калькулятор",
        "Я же просил не закрывать браузер",
        "нет, не так",
        "Ты ошибся, это другой файл",
        "I meant the other window",
        "No, I said the second one",
        "That's not what I asked",
        "Это неправильно",
    ],
)
def test_correction_markers_are_detected(text):
    assert lessons.looks_like_correction(text)


@pytest.mark.parametrize(
    "text",
    [
        "Что не так с компьютером?",
        "Почему неправильно работает Wi-Fi?",
        "Открой браузер",
        "Расскажи, как работает калькулятор",
        "What is wrong with the disk?",
    ],
)
def test_questions_are_not_mistaken_for_corrections(text):
    assert not lessons.looks_like_correction(text)


def test_user_correction_keeps_the_exchange_it_refers_to(store):
    item = lessons.record_user_correction(
        "Не то, я просил калькулятор",
        previous_request="Открой блокнот",
        previous_reply="Открыл блокнот.",
    )

    assert item is not None
    assert item["kind"] == "correction"
    assert "блокнот" in item["trigger"]
    assert "Открыл блокнот" in item["mistake"]
    assert item["fix"] == "калькулятор"
    assert "Не то" not in item["fix"]

    prompt = lessons.format_lessons_for_prompt()
    assert "Owner correction" in prompt
    assert 'after "Открой блокнот"' in prompt


def test_forget_and_clear_auto_keep_owner_rules(store):
    lessons.remember_rule("Never close the browser without asking")
    lessons.record_tool_failure("open_app", "boom")

    assert lessons.forget("open_app") == 1
    assert len(lessons.load_lessons()) == 1

    lessons.record_tool_failure("web_search", "another boom")
    removed = lessons.clear_auto()
    assert removed == 1
    items = lessons.load_lessons()
    assert len(items) == 1
    assert items[0]["kind"] == "explicit"


def test_reflection_stores_only_valid_items(store, monkeypatch):
    from core import gemini

    monkeypatch.setattr(
        gemini,
        "as_json",
        lambda *_args, **_kwargs: [
            {
                "trigger": "owner asks for a calculator",
                "mistake": "opened the wrong app",
                "fix": "Open the calculator named by the owner, not the last app.",
                "scope": "open_app",
            },
            {"fix": "", "mistake": "junk"},
            "not a dict",
        ],
    )
    transcript = (
        "User: Не то, я просил калькулятор\n"
        "JARVIS: Открыл блокнот."
    )

    learned = lessons.extract_lessons_from_transcript(transcript, "Russian")

    assert len(learned) == 1
    assert learned[0]["kind"] == "reflection"
    assert learned[0]["scope"] == "open_app"
    assert "calculator" in learned[0]["fix"]


def test_reflection_does_not_call_the_model_for_a_clean_chat(store, monkeypatch):
    from core import gemini

    def fail(*_args, **_kwargs):
        raise AssertionError("reflection should not run for a clean conversation")

    monkeypatch.setattr(gemini, "as_json", fail)

    assert lessons.extract_lessons_from_transcript(
        "User: Привет\nJARVIS: Здравствуйте.", "Russian"
    ) == []


def test_prompt_stays_within_budget(store):
    for index in range(20):
        lessons.remember_rule(f"Owner rule number {index} with a reasonably long wording")

    prompt = lessons.format_lessons_for_prompt()
    assert prompt
    assert len(prompt) <= lessons._PROMPT_MAX_CHARS + 2


def test_pruning_never_drops_an_owner_rule(store, monkeypatch):
    monkeypatch.setattr(lessons, "_MAX_LESSONS", 3)
    owner = lessons.remember_rule("Never delete files without asking")
    for index in range(6):
        lessons.record(
            kind="reflection",
            trigger=f"case {index}",
            mistake="wrong app",
            fix=f"Use approach {index}",
            confidence=0.9,
        )

    items = lessons.load_lessons()
    assert len(items) <= 3
    assert any(item["id"] == owner["id"] for item in items)
