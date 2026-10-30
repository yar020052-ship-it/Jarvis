"""Tests for the background_tasks action wrapper."""

from __future__ import annotations

from actions import background_tasks


def test_status_lists_safe_tasks():
    result = background_tasks.background_tasks({"op": "status"})
    assert "lesson_stats" in result
    assert "ping" in result
    assert "model_status" in result


def test_run_ping_returns_pong():
    result = background_tasks.background_tasks({"op": "run", "task": "ping"})
    assert "pong" in result


def test_unknown_task_is_rejected():
    result = background_tasks.background_tasks({"op": "run", "task": "rm-rf"})
    assert "Неизвестная фоновая задача" in result


def test_invalid_op_raises():
    try:
        background_tasks.background_tasks({"op": "explode"})
    except RuntimeError as exc:
        assert "status" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")