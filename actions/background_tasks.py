"""Owner-facing control over the background task scheduler.

Only an explicit whitelist of harmless tasks can be launched from voice/text.
Destructive maintenance (file cleanup, shutdown, network changes) is deliberately
not exposed here.
"""

from __future__ import annotations

import asyncio

from background import BackgroundTask, BackgroundTaskManager, ResourceLimits

_MANAGER = BackgroundTaskManager(max_concurrent=2)


async def _ping(**_params) -> str:
    return "pong"


async def _lesson_stats(**_params) -> str:
    from memory import lessons

    summary = lessons.stats()
    return (
        f"Уроков: {summary['total']}, открытых сбоев: {summary['open']}, "
        f"правил владельца: {summary['owner']}."
    )


async def _model_status(**_params) -> str:
    try:
        from core import gemini  # noqa: F401

        return "Клиент Gemini доступен."
    except Exception as exc:  # noqa: BLE001
        return f"Клиент Gemini недоступен: {exc}"


_TASKS = {
    "ping": _ping,
    "lesson_stats": _lesson_stats,
    "model_status": _model_status,
}


def _run(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # An event loop is already running in this thread; fall back to a new
        # thread with its own loop.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=60)


def background_tasks(parameters: dict | None = None, **_ctx) -> str:
    params = parameters or {}
    op = str(params.get("op", "status")).strip().lower()

    if op in {"status", "list"}:
        running = _MANAGER.get_running()
        tasks = ", ".join(sorted(_TASKS))
        history = _MANAGER.get_history(3)
        last = ""
        if history:
            last = (
                f" Последний запуск: {history[-1].get('id')} — "
                f"{history[-1].get('status')}."
            )
        return (
            f"Фоновый планировщик: задач в работе {len(running)}"
            + (f" ({', '.join(running)})" if running else "")
            + f". Доступные безопасные задачи: {tasks}.{last}"
        )

    if op in {"run", "start"}:
        task_id = str(params.get("task") or params.get("id") or "ping").strip().lower()
        if task_id not in _TASKS:
            return f"Неизвестная фоновая задача: {task_id}. Доступны: {', '.join(sorted(_TASKS))}."
        if task_id in _MANAGER.get_running():
            return f"Фоновая задача {task_id} уже выполняется."
        task = BackgroundTask(
            id=task_id,
            name=task_id,
            coroutine=_TASKS[task_id],
            limits=ResourceLimits(max_retries=1, retry_delay_seconds=1.0),
            timeout_seconds=30,
        )
        _MANAGER.register(task)
        result = _run(_MANAGER.execute(task_id))
        if result is None:
            return f"Фоновая задача {task_id} не завершилась. Смотри журнал."
        return f"Фоновая задача {task_id}: {result}"

    if op in {"stop", "cancel", "stop_all"}:
        _MANAGER.stop_all()
        return "Остановка фоновых задач запрошена."

    raise RuntimeError("op должен быть: status, run или stop.")


TOOL = {
    "name": "background_tasks",
    "description": (
        "Управляет безопасными фоновыми задачами JARVIS. op=status показывает "
        "состояние планировщика; op=run запускает одну задачу из белого списка "
        "(ping, lesson_stats, model_status); op=stop запрашивает остановку. "
        "Не используй для опасных операций: удаление файлов, выключение, смена сети."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "op": {
                "type": "STRING",
                "description": "status, run или stop",
            },
            "task": {
                "type": "STRING",
                "description": "Имя задачи для op=run: ping, lesson_stats или model_status",
            },
        },
        "required": ["op"],
    },
    "handler": background_tasks,
}