"""Compatibility shim: the learning tool is also available as ``lessons``."""

from __future__ import annotations

from actions import learning as _learning


def lessons(parameters: dict | None = None, **_ctx) -> str:
    return _learning.learning(parameters=parameters, **_ctx)


TOOL = dict(_learning.TOOL)
TOOL["name"] = "lessons"
TOOL["handler"] = lessons