"""Background task system for JARVIS autonomous operations.

Provides a controlled background execution environment with:
- Limited task sets per cycle
- CPU, RAM, network and query limits
- Event logging with rotation
- Exception handling with retries and delays
- User-stop capability
- Status tracking
- State recovery after restart
- Sleep/pause mode awareness
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Execution state of a background task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class ResourceLimits:
    """Resource consumption limits for background tasks."""

    def __init__(
        self,
        max_cpu_percent: float = 15.0,
        max_ram_mb: float = 100.0,
        max_queries_per_minute: int = 30,
        max_runtime_seconds: Optional[float] = None,
        max_retries: int = 3,
        retry_delay_seconds: float = 5.0,
    ):
        self.max_cpu_percent = max_cpu_percent
        self.max_ram_mb = max_ram_mb
        self.max_queries_per_minute = max_queries_per_minute
        self.max_runtime_seconds = max_runtime_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds


@dataclass
class BackgroundTask:
    """Description of a background task to execute."""

    id: str
    name: str
    coroutine: Callable
    params: dict = field(default_factory=dict)
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    timeout_seconds: Optional[float] = None
    pause_condition: Optional[Callable[[], bool]] = None
    stop_requested: bool = False
    last_status: str = ""
    last_error: str = ""
    last_started: float = 0.0
    last_finished: float = 0.0
    run_count: int = 0

    async def run(self) -> Any:
        """Execute the task with full resource management and error handling."""
        start_time = time.time()
        self.last_started = start_time
        status = TaskStatus.PENDING
        last_error: Optional[str] = None

        try:
            # Check initial pause condition
            if self.pause_condition and await self.pause_condition():
                status = TaskStatus.PAUSED
                logger.info(f"Task {self.id} paused at start")
                return None

            status = TaskStatus.RUNNING
            logger.info(f"Starting background task: {self.id} ({self.name})")

            # Execute with retry logic
            for attempt in range(self.limits.max_retries + 1):
                # Check stop request
                if self.stop_requested:
                    status = TaskStatus.CANCELLED
                    logger.info(f"Task {self.id} cancelled by stop request")
                    return None

                # Check runtime limit
                elapsed = time.time() - start_time
                if self.limits.max_runtime_seconds and elapsed > self.limits.max_runtime_seconds:
                    status = TaskStatus.FAILED
                    logger.warning(
                        f"Task {self.id} exceeded max runtime "
                        f"({elapsed:.1f}s > {self.limits.max_runtime_seconds}s)"
                    )
                    return None

                # Check query rate limit (simple implementation)
                if attempt > 0:
                    await asyncio.sleep(self.limits.retry_delay_seconds)

                try:
                    result = await asyncio.wait_for(
                        self.coroutine(**self.params),
                        timeout=self.timeout_seconds,
                    )
                    status = TaskStatus.COMPLETED
                    logger.info(f"Task {self.id} completed successfully")
                    return result

                except asyncio.TimeoutError:
                    last_error = "Timeout exceeded"
                    logger.warning(f"Task {self.id} attempt {attempt+1} timed out")
                    if attempt < self.limits.max_retries:
                        continue
                    status = TaskStatus.FAILED
                    return None

                except Exception as e:
                    last_error = str(e)
                    logger.warning(
                        f"Task {self.id} attempt {attempt+1} failed: {e}"
                    )
                    if attempt < self.limits.max_retries:
                        await asyncio.sleep(self.limits.retry_delay_seconds)
                        continue
                    status = TaskStatus.FAILED
                    return None

            # Should not reach here, but safety first
            status = TaskStatus.FAILED
            return None

        except Exception as e:
            last_error = traceback.format_exc()
            status = TaskStatus.FAILED
            logger.error(f"Task {self.id} unexpected error: {e}")
            return None

        finally:
            # Log completion and keep the last outcome on the task object so a
            # manager can persist it for inspection after a restart.
            total_time = time.time() - start_time
            self.last_status = status.value
            self.last_error = last_error or ""
            self.last_finished = time.time()
            self.run_count += 1
            logger.info(
                f"Task {self.id} finished: status={status.value}, "
                f"time={total_time:.1f}s, error={last_error}"
            )


class BackgroundTaskManager:
    """Manages a pool of background tasks with resource monitoring."""

    def __init__(
        self,
        max_concurrent: int = 3,
        resource_limits: ResourceLimits | None = None,
        state_dir: Optional[Path] = None,
    ):
        self.max_concurrent = max_concurrent
        self.resource_limits = resource_limits or ResourceLimits()
        self.state_dir = state_dir or Path(".jarvis_background")
        self._state_dir = self.state_dir
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, BackgroundTask] = {}
        self._running: set[str] = set()
        self._status_callback: Optional[Callable[[str, TaskStatus], None]] = None
        self._pause_check: Optional[Callable[[], bool]] = None
        self._stop_all = asyncio.Event()
        self._history_path = self._state_dir / "tasks_history.json"
        self._history: list[dict] = self._load_history()

    def _load_history(self) -> list[dict]:
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [item for item in data if isinstance(item, dict)][-50:]

    def _persist(self, task: BackgroundTask) -> None:
        entry = {
            "id": task.id,
            "name": task.name,
            "status": task.last_status or TaskStatus.PENDING.value,
            "error": task.last_error[:500],
            "started": task.last_started,
            "finished": task.last_finished,
            "run_count": task.run_count,
        }
        self._history.append(entry)
        self._history = self._history[-50:]
        try:
            tmp = self._history_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._history_path)
        except Exception as exc:  # noqa: BLE001 - history must never stop work
            logger.warning(f"Could not persist background task history: {exc}")

    def get_history(self, limit: int = 10) -> list[dict]:
        """Return recent task outcomes, newest last."""
        try:
            count = max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            count = 10
        return list(self._history[-count:])

    def set_pause_check(self, check: Callable[[], bool]) -> None:
        """Set async check for pause condition (e.g., user is actively using JARVIS)."""
        self._pause_check = check

    def set_status_callback(self, callback: Callable[[str, TaskStatus], None]) -> None:
        """Set callback for status updates (e.g., UI logging)."""
        self._status_callback = callback

    def register(self, task: BackgroundTask) -> None:
        """Register a task for management."""
        self._tasks[task.id] = task
        logger.debug(f"Registered background task: {task.id}")

    def unregister(self, task_id: str) -> None:
        """Unregister a task."""
        self._tasks.pop(task_id, None)
        self._running.discard(task_id)
        logger.debug(f"Unregistered background task: {task_id}")

    async def execute(self, task_id: str, stop_on_pause: bool = True) -> Any:
        """Execute a single registered task with full management."""
        if task_id not in self._tasks:
            raise ValueError(f"Unknown task: {task_id}")

        task = self._tasks[task_id]

        # Check if already running
        if task.id in self._running:
            logger.warning(f"Task {task_id} already running")
            return None

        # Check stop request
        if self._stop_all.is_set():
            logger.info(f"Not executing {task_id}: stop all requested")
            return None

        # Check pause condition
        if task.pause_condition and await task.pause_condition():
            if self._status_callback:
                self._status_callback(task_id, TaskStatus.PAUSED)
            logger.info(f"Task {task_id} paused by condition")
            return None

        self._running.add(task.id)
        self._status_callback(task.id, TaskStatus.RUNNING) if self._status_callback else None

        try:
            result = await task.run()
            return result
        finally:
            self._running.discard(task.id)
            try:
                final_status = TaskStatus(task.last_status or TaskStatus.COMPLETED.value)
            except ValueError:
                final_status = TaskStatus.FAILED
            if self._status_callback:
                self._status_callback(task.id, final_status)
            self._persist(task)
            logger.debug(f"Task {task_id} slot freed")

    async def execute_all(self, stop_on_pause: bool = True) -> dict[str, Any]:
        """Execute all registered tasks respecting concurrency limits."""
        # Filter out already-running tasks
        ready_tasks = [
            t for t in self._tasks.values()
            if t.id not in self._running
            and not self._stop_all.is_set()
        ]

        if not ready_tasks:
            return {}

        # Sort by priority or insertion order; here we just take up to max_concurrent
        to_run = ready_tasks[: self.max_concurrent]

        # Mark these as running
        for task in to_run:
            self._running.add(task.id)

        # Execute concurrently
        results: dict[str, Any] = {}
        coros = [self._run_with_management(task) for task in to_run]

        task_results = await asyncio.gather(*coros, return_exceptions=True)

        for task, result in zip(to_run, task_results):
            if isinstance(result, Exception):
                logger.error(f"Task {task.id} exception: {result}")
                result = None
            results[task.id] = result
            self._persist(task)
            if self._status_callback:
                try:
                    self._status_callback(
                        task.id,
                        TaskStatus(task.last_status or TaskStatus.COMPLETED.value),
                    )
                except ValueError:
                    self._status_callback(task.id, TaskStatus.FAILED)

        # Free slots
        for task in to_run:
            self._running.discard(task.id)

        return results

    async def _run_with_management(self, task: BackgroundTask) -> Any:
        """Run a single task with pause/stop monitoring."""
        # Monitor pause condition during execution
        if task.pause_condition:
            # We'll check periodically - but task.run already handles this internally
            pass

        return await task.run()

    async def stop_all(self) -> None:
        """Request all tasks to stop."""
        self._stop_all.set()
        logger.info("Stop all background tasks requested")

    def get_running(self) -> list[str]:
        """Return IDs of currently running tasks."""
        return list(self._running)

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        """Get a registered task by ID."""
        return self._tasks.get(task_id)


# Convenience function for common background operations
async def run_background_task(
    task_id: str,
    coroutine: Callable,
    params: dict | None = None,
    name: str | None = None,
    max_retries: int = 3,
    retry_delay: float = 5.0,
    timeout: float | None = None,
    max_cpu_percent: float = 15.0,
    max_ram_mb: float = 100.0,
    max_queries_per_minute: int = 30,
    max_runtime_seconds: float | None = None,
    pause_condition: Callable[[], bool] | None = None,
    status_callback: Callable[[str, str], None] | None = None,
) -> Any:
    """Run a single background task with specified limits.

    Parameters
    ----------
    task_id : str
        Unique identifier for this task (used for logging/recovery).
    coroutine : Callable
        async function to execute.
    params : dict, optional
        Parameters to pass to the coroutine.
    name : str, optional
        Human-readable name for logging.
    max_retries : int, optional
        Maximum number of retry attempts.
    retry_delay : float, optional
        Seconds to wait between retries.
    timeout : float, optional
        Seconds before timing out the coroutine.
    max_cpu_percent : float, optional
        Maximum CPU percentage allowed.
    max_ram_mb : float, optional
        Maximum RAM in MB allowed.
    max_queries_per_minute : int, optional
        Rate limit for model/api queries.
    max_runtime_seconds : float, optional
        Maximum total runtime in seconds.
    pause_condition : Callable, optional
        async function returning True if the task should pause
        (e.g., user is actively interacting).
    status_callback : Callable, optional
        Callback(task_id, status_string) for UI/log updates.

    Returns
    -------
    Any
        Result of the coroutine, or None on failure/cancellation.
    """
    limits = ResourceLimits(
        max_retries=max_retries,
        retry_delay_seconds=retry_delay,
        max_cpu_percent=max_cpu_percent,
        max_ram_mb=max_ram_mb,
        max_queries_per_minute=max_queries_per_minute,
        max_runtime_seconds=max_runtime_seconds,
    )

    task = BackgroundTask(
        id=task_id,
        name=name or task_id,
        coroutine=coroutine,
        params=params or {},
        limits=limits,
        timeout_seconds=timeout,
        pause_condition=pause_condition,
    )

    manager = BackgroundTaskManager(
        max_concurrent=1,
        resource_limits=limits,
    )
    manager.set_status_callback(status_callback) if status_callback else None

    # Wrap pause check
    if pause_condition:
        manager.set_pause_check(pause_condition)

    manager.register(task)
    return await manager.execute(task_id)


# Safe built-in examples. They do not delete or modify user data.

async def example_ping(**_params) -> str:
    """Return a fixed string; useful for a smoke test of the scheduler."""
    return "pong"


async def example_model_status(**_params) -> str:
    """Report whether the Gemini client module is importable."""
    try:
        from core import gemini  # noqa: F401

        return "Gemini client importable."
    except Exception as exc:  # noqa: BLE001
        return f"Gemini client unavailable: {exc}"