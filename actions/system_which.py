"""Resolve executable names on PATH without launching them."""

from __future__ import annotations

import json
import re
import shutil


_BIN_NAME = re.compile(r"^[A-Za-z0-9_.+-]{1,128}$")


def system_which(parameters: dict | None = None, **_ctx) -> str:
    p = parameters or {}
    requested = p.get("bins")
    if isinstance(requested, str):
        requested = [requested]
    if not isinstance(requested, list):
        raise ValueError("bins должен быть списком имён исполняемых файлов.")
    if len(requested) > 64:
        raise ValueError("За один запрос можно проверить не более 64 имён.")

    found: dict[str, str] = {}
    for item in requested:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not _BIN_NAME.fullmatch(name):
            continue
        resolved = shutil.which(name)
        if resolved:
            found[name] = resolved

    return json.dumps({"bins": found}, ensure_ascii=False)


TOOL = {
    "name": "system_which",
    "description": (
        "Resolves executable names to absolute paths by searching PATH, "
        "including Windows PATHEXT. Read-only: it never launches a program. "
        "Use it to check whether a tool is installed or where it is located."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "bins": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Executable names to locate on PATH.",
            }
        },
        "required": ["bins"],
    },
    "handler": system_which,
}
