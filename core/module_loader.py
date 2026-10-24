"""Shared helpers for discovering Python modules in a drop-in directory."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Iterator


def iter_module_files(directory: Path) -> list[Path]:
    """Return sorted *.py files in ``directory``, skipping underscore-prefixed names."""
    directory.mkdir(parents=True, exist_ok=True)
    return sorted(
        (path for path in directory.glob("*.py") if not path.name.startswith("_")),
        key=lambda path: path.name,
    )


def load_module(path: Path, module_name: str):
    """Import a module from ``path``; removes it from sys.modules on failure."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("could not build import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module