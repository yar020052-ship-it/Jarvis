"""Tests for the background task system."""

from __future__ import annotations

import asyncio

from background import (
    BackgroundTask,
    BackgroundTaskManager,
    ResourceLimits,
    run_background_task,
)


def test_successful_background_task_returns_result():
    async def work(**_params):
        await asyncio.sleep(0)
        return "ok"

    assert asyncio.run(run_background_task("test-ok", work)) == "ok"


def test_failing_task_returns_none_after_retries():
    attempts = []

    async def work(**_params):
        attempts.append(1)
        raise RuntimeError("boom")

    result = asyncio.run(
        run_background_task(
            "test-fail",
            work,
            max_retries=2,
            retry_delay=0.01,
        )
    )

    assert result is None
    assert len(attempts) == 3


def test_task_stops_after_runtime_limit():
    async def slow(**_params):
        await asyncio.sleep(0.2)
        return "late"

    result = asyncio.run(
        run_background_task(
            "test-timeout",
            slow,
            timeout=0.01,
            max_retries=0,
        )
    )

    assert result is None


def test_manager_runs_no_more_than_max_concurrent():
    manager = BackgroundTaskManager(max_concurrent=2)
    started = []

    async def work(name, **_params):
        started.append(name)
        await asyncio.sleep(0.02)
        return name

    for name in ("a", "b", "c"):
        manager.register(
            BackgroundTask(
                id=name,
                name=name,
                coroutine=lambda name=name: work(name),
            )
        )

    results = asyncio.run(manager.execute_all())

    assert set(results) == {"a", "b"}
    assert started == ["a", "b"]


def test_manager_rejects_unknown_task():
    manager = BackgroundTaskManager()

    try:
        asyncio.run(manager.execute("missing"))
    except ValueError as exc:
        assert "Unknown task" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_resource_limits_defaults_are_bounded():
    limits = ResourceLimits()
    assert limits.max_cpu_percent > 0
    assert limits.max_ram_mb > 0
    assert limits.max_queries_per_minute > 0
    assert limits.max_retries >= 0


def test_manager_persists_task_history(tmp_path):
    manager = BackgroundTaskManager(state_dir=tmp_path)

    async def work(**_params):
        return "persisted"

    manager.register(BackgroundTask(id="history", name="History", coroutine=work))
    assert asyncio.run(manager.execute("history")) == "persisted"

    history = manager.get_history()
    assert history and history[-1]["id"] == "history"
    assert history[-1]["status"] == "completed"

    reloaded = BackgroundTaskManager(state_dir=tmp_path)
    assert reloaded.get_history()[-1]["id"] == "history"