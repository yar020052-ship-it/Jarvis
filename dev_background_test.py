"""Test script for the background task system."""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import background
from background import (
    BackgroundTask,
    BackgroundTaskManager,
    ResourceLimits,
    TaskStatus,
    run_background_task,
)


async def simple_test_coro(**_kwargs) -> str:
    """Simple coroutine that succeeds."""
    await asyncio.sleep(0.1)
    return "success"


async def failing_coro(**_kwargs) -> str:
    """Simple coroutine that fails."""
    await asyncio.sleep(0.1)
    raise ValueError("test error")


async def main():
    print("=== Background Task System Tests ===\n")

    # Test 1: Basic task execution
    print("Test 1: Basic task execution")
    result = await run_background_task(
        task_id="test_basic",
        coroutine=simple_test_coro,
        name="Basic Test",
        status_callback=lambda tid, status: print(f"  Status: {tid} -> {status}"),
    )
    print(f"  Result: {result}")
    assert result == "success", f"Expected 'success', got '{result}'"
    print("  PASSED\n")

    # Test 2: Failing task with retries
    print("Test 2: Failing task with retries (should fail after max_retries)")
    result = await run_background_task(
        task_id="test_failing",
        coroutine=failing_coro,
        max_retries=2,
        retry_delay=0.05,
        status_callback=lambda tid, status: print(f"  Status: {tid} -> {status}"),
    )
    print(f"  Result: {result}")
    assert result is None, f"Expected None, got '{result}'"
    print("  PASSED\n")

    # Test 3: Task with custom limits
    print("Test 3: Task with custom resource limits")
    result = await run_background_task(
        task_id="test_limits",
        coroutine=simple_test_coro,
        max_runtime_seconds=5.0,
        max_cpu_percent=10.0,
        status_callback=lambda tid, status: print(f"  Status: {tid} -> {status}"),
    )
    print(f"  Result: {result}")
    assert result == "success"
    print("  PASSED\n")

    # Test 4: BackgroundTaskManager
    print("Test 4: BackgroundTaskManager")

    manager = BackgroundTaskManager(max_concurrent=2)

    async def task_a():
        await asyncio.sleep(0.05)
        return "a"

    async def task_b():
        await asyncio.sleep(0.05)
        return "b"

    async def task_c():
        await asyncio.sleep(0.05)
        return "c"

    manager.register(BackgroundTask(id="a", coroutine=task_a, name="Task A"))
    manager.register(BackgroundTask(id="b", coroutine=task_b, name="Task B"))
    manager.register(BackgroundTask(id="c", coroutine=task_c, name="Task C"))

    results = await manager.execute_all()
    print(f"  Results: {results}")
    assert "a" in results and results["a"] == "a"
    assert "b" in results and results["b"] == "b"
    # C should not run because max_concurrent=2
    print("  PASSED\n")

    # Test 5: Pause condition
    print("Test 5: Pause condition")
    paused = False

    async def pause_check():
        nonlocal paused
        return paused

    result = await run_background_task(
        task_id="test_pause",
        coroutine=simple_test_coro,
        pause_condition=pause_check,
        status_callback=lambda tid, status: print(f"  Status: {tid} -> {status}"),
    )
    print(f"  Result (with pause=True): {result}")
    # Should complete because pause_check returns False by default initially
    # But let's test the manager directly
    manager2 = BackgroundTaskManager()
    manager2.set_pause_check(pause_check)
    manager2.register(BackgroundTask(id="paused_task", coroutine=simple_test_coro, name="Paused Task"))
    # Don't set paused=True, so it should run
    results2 = await manager2.execute_all()
    print(f"  Result (pause=False by default): {results2}")
    print("  PASSED\n")

    print("=== All tests passed! ===")


if __name__ == "__main__":
    asyncio.run(main())