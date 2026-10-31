"""Read JARVIS's own log to detect incidents and errors.

Allows JARVIS to see its own log output and identify problems.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.paths import get_app_dir


def read_log(parameters: dict | None = None, **_ctx) -> str:
    """Read the last N lines of JARVIS's log file.

    Parameters:
        lines: number of lines to read (default: 50)
        filter: optional filter string (e.g., "Error", "AttributeError")
    """
    params = parameters or {}
    n = int(params.get("lines", 50))
    filter_str = params.get("filter", "").strip().lower()

    # Try common log locations
    log_paths = [
        Path.home() / "AppData" / "Local" / "JARVIS" / "logs" / "jarvis.log",
        get_app_dir() / "logs" / "jarvis.log",
        get_app_dir() / "jarvis.log",
    ]

    for log_path in log_paths:
        if not log_path.exists():
            continue
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if filter_str:
                lines = [l for l in lines if filter_str in l.lower()]
            tail = lines[-n:] if len(lines) > n else lines
            if tail:
                return f"Log ({log_path.name}, {len(lines)} lines total):\n" + "\n".join(tail)
        except Exception as e:
            continue

    # Fallback: try to find any .log file in the logs directory
    logs_dir = Path.home() / "AppData" / "Local" / "JARVIS" / "logs"
    if logs_dir.exists():
        log_files = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for lf in log_files[:3]:
            try:
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
                if filter_str:
                    lines = [l for l in lines if filter_str in l.lower()]
                tail = lines[-n:] if len(lines) > n else lines
                if tail:
                    return f"Log ({lf.name}):\n" + "\n".join(tail)
            except Exception:
                continue

    return "Лог-файл не найден. JARVIS может не записывать лог в файл."


TOOL = {
    "name": "read_log",
    "description": (
        "Read JARVIS's own log file to detect errors, incidents, and issues. "
        "Use filter to search for specific errors (e.g., 'Error', 'AttributeError'). "
        "Use lines to control how many lines to read (default: 50)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "lines": {"type": "INTEGER", "description": "Number of lines to read (default: 50)"},
            "filter": {"type": "STRING", "description": "Filter string to search for in log"},
        },
        "required": [],
    },
    "handler": read_log,
}
