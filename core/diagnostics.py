"""Small helpers for reporting failures without swallowing them silently."""

from __future__ import annotations

import logging
import traceback
from typing import Any


_LOGGER = logging.getLogger("jarvis")


def log_exception(context: str, exc: BaseException, *, level: int = logging.WARNING) -> None:
    """Log an exception with module/operation context and a stack trace."""
    _LOGGER.log(level, "%s failed: %s", context, exc, exc_info=exc)
    try:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    except Exception:  # pragma: no cover - logging must never raise
        pass


def increment(metric: str, amount: int = 1, store: dict[str, int] | None = None) -> None:
    """Best-effort metric counter; defaults to an in-process dict."""
    target = store if store is not None else _METRICS
    target[metric] = target.get(metric, 0) + amount


_METRICS: dict[str, int] = {}


def metrics_snapshot() -> dict[str, int]:
    return dict(_METRICS)