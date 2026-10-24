"""Проверка здоровья — ворота для любого изменения кода.

Ни одно исправление не считается принятым, пока не пройдено:
  syntax — компиляция корневых файлов UI/рантайма и .py в рабочих модулях
  import — импорт dashboard.server и actions.phone_mic (тяжёлые модули не трогаем)
  smoke — если для исправленного файла нет тестов, изолированный импорт и
          проверка контракта TOOL для action-модулей
"""
from __future__ import annotations

import subprocess
import sys
import time
import re
import json
from dataclasses import dataclass, field

from . import BASE_DIR

CHECK_DIRS = ("actions", "plugins", "core", "dashboard", "autonomy", "tests")
CHECK_FILES = ("main.py", "ui.py")
SKIP_DIRS = {"__pycache__", ".venv", "node_modules"}


@dataclass
class Check:
    name: str
    ok: bool
    ms: int
    detail: str = ""


@dataclass
class HealthReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def text(self) -> str:
        lines = []
        for c in self.checks:
            mark = "OK  " if c.ok else "FAIL"
            tail = f" — {c.detail}" if c.detail else ""
            lines.append(f"[{mark}] {c.name} ({c.ms} мс){tail}")
        lines.append("ИТОГ: " + ("здоров" if self.ok else "ЕСТЬ ПРОБЛЕМЫ"))
        return "\n".join(lines)


def _smoke_check(changed_rel: str, required_symbols: list[str] | None = None) -> Check:
    started = time.time()
    relative_module = changed_rel.replace("\\", "/").removesuffix(".py")
    if not re.fullmatch(r"[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*", relative_module):
        return Check("smoke", False, int((time.time() - started) * 1000),
                     "недопустимое имя модуля")

    module = relative_module.replace("/", ".")
    probe = (
        "import importlib, json, sys\n"
        "m = importlib.import_module(sys.argv[1])\n"
        "required = json.loads(sys.argv[2])\n"
        "for name in required:\n"
        "    assert hasattr(m, name), f'missing public symbol: {name}'\n"
        "if sys.argv[1].startswith('actions.'):\n"
        "    tool = getattr(m, 'TOOL', None)\n"
        "    if tool is not None:\n"
        "        assert isinstance(tool, dict), 'TOOL must be a dict'\n"
        "        assert isinstance(tool.get('name'), str) and tool['name'].strip(), 'TOOL.name is missing'\n"
        "        assert callable(tool.get('handler')), 'TOOL.handler is not callable'\n"
        "        assert isinstance(tool.get('parameters'), dict), 'TOOL.parameters is missing'\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe, module,
             json.dumps(required_symbols or [])],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30,
        )
        detail = "изолированный импорт; публичные символы и контракт TOOL сохранены"
        if result.returncode:
            detail = (result.stderr or result.stdout or "smoke-проверка не прошла")[-1000:]
        return Check("smoke", result.returncode == 0,
                     int((time.time() - started) * 1000), detail)
    except Exception as e:  # noqa: BLE001
        return Check("smoke", False, int((time.time() - started) * 1000), str(e))


def run_health(changed_rel: str | None = None, functional: bool = False,
               required_symbols: list[str] | None = None) -> HealthReport:
    rep = HealthReport()
    t0 = time.time()
    bad: list[str] = []
    files = 0
    source_paths = [BASE_DIR / name for name in CHECK_FILES]
    for sub in CHECK_DIRS:
        root = BASE_DIR / sub
        if root.exists():
            source_paths.extend(root.rglob("*.py"))
    for path in sorted(source_paths):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        files += 1
        try:
            with open(path, "rb") as f:
                compile(f.read(), str(path), "exec")
        except SyntaxError as e:
            bad.append(f"{path.relative_to(BASE_DIR).as_posix()}: {e}")
    rep.checks.append(Check("syntax", not bad, int((time.time() - t0) * 1000),
                            "; ".join(bad[:3]) if bad else f"файлов: {files}"))
    t1 = time.time()
    modules = ["dashboard.server", "actions.phone_mic"]
    if changed_rel and changed_rel.endswith(".py"):
        relative_module = changed_rel.replace("\\", "/").removesuffix(".py")
        if not re.fullmatch(r"[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*", relative_module):
            modules.append("")
        else:
            module = relative_module.replace("/", ".")
            if module not in modules:
                modules.append(module)
    import_errors = []
    for mod in modules:
        if not mod:
            import_errors.append(f"{changed_rel}: недопустимое имя модуля")
            continue
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import {mod}"], cwd=str(BASE_DIR),
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "импорт завершился с ошибкой")[-500:]
                import_errors.append(f"{mod}: {detail}")
        except Exception as e:  # noqa: BLE001
            import_errors.append(f"{mod}: {e}")
    rep.checks.append(Check("import", not import_errors,
                            int((time.time() - t1) * 1000),
                            "; ".join(import_errors[:2])))

    if functional and changed_rel and changed_rel.endswith(".py"):
        t2 = time.time()
        test_paths = []
        changed = (BASE_DIR / changed_rel).resolve()
        stem = changed.stem
        expected_names = {f"test_{stem}.py", f"{stem}_test.py"}
        for root_name in ("tests", "test"):
            root = BASE_DIR / root_name
            if root.exists():
                test_paths.extend(p for p in root.rglob("*.py")
                                  if p.is_file() and p.name in expected_names)
        parent = changed.parent
        test_paths.extend(p for p in parent.glob("*.py")
                          if p.is_file() and p.name in expected_names)
        # This project has no checked-in unit-test suite for most modules. Do not
        # make self-repair impossible for every such file: require an isolated
        # import smoke check and validate the public action contract instead.
        if not test_paths:
            check = _smoke_check(changed_rel, required_symbols)
            check.detail = "тестов нет; " + check.detail
            rep.checks.append(check)
        else:
            rep.checks.append(_smoke_check(changed_rel, required_symbols))
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", *map(str, sorted(set(test_paths)))],
                    cwd=str(BASE_DIR), capture_output=True, text=True, timeout=120,
                )
                detail = "" if result.returncode == 0 else (
                    (result.stdout + "\n" + result.stderr)[-1000:] or "pytest завершился с ошибкой")
                rep.checks.append(Check("functional", result.returncode == 0,
                                        int((time.time() - t2) * 1000), detail))
            except Exception as e:  # noqa: BLE001
                rep.checks.append(Check("functional", False, int((time.time() - t2) * 1000), str(e)))
    return rep
