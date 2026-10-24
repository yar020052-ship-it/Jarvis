"""Focused, read-only diagnostics for processes, storage, and startup apps."""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path

import psutil


def _top_processes() -> str:
    processes = []
    # Prime per-process CPU counters, then take a short sample.
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            proc.cpu_percent(None)
            processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.35)
    rows = []
    for proc in processes:
        try:
            info = proc.info
            rows.append((proc.cpu_percent(None), proc.memory_info().rss, info["pid"], info["name"] or "(без имени)"))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    rows.sort(reverse=True)
    return "\n".join(f"{name} (PID {pid}): CPU {cpu:.1f}%, RAM {rss / 1024**2:.0f} МБ" for cpu, rss, pid, name in rows[:12]) or "Нет доступных процессов."


def _storage() -> str:
    rows = []
    for part in psutil.disk_partitions(all=False):
        if not part.mountpoint:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        rows.append(f"{part.device} ({part.mountpoint}): свободно {usage.free / 1024**3:.1f} из {usage.total / 1024**3:.1f} ГБ ({100-usage.percent:.0f}%)")
    return "\n".join(rows) or "Не удалось прочитать сведения о дисках."


def _startup() -> str:
    if platform.system() == "Windows":
        try:
            import winreg
        except ImportError:
            return "Реестр автозагрузки недоступен в этой сборке Python."
        rows = []
        locations = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Пользователь"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Компьютер"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Компьютер 32-bit"),
        )
        for hive, key_path, scope in locations:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, value, _ = winreg.EnumValue(key, index)
                        command = str(value).strip()
                        # Do not pass arbitrary arguments (which can contain tokens)
                        # into the assistant context.
                        executable = (command.split('"', 2)[1] if command.startswith('"') and command.count('"') >= 2
                                      else (command.split(maxsplit=1)[0] if command else "(пустой путь)"))
                        rows.append(f"{scope}: {name} — {executable[:150]}")
            except OSError:
                continue
        return "\n".join(rows[:40]) or "В стандартных разделах Run записей нет. Это не включает Планировщик задач и папки автозагрузки."
    return "Перечень автозагрузки поддерживается пока только в Windows."


def system_diagnostics(parameters=None, player=None) -> str:
    action = str((parameters or {}).get("action", "overview")).lower().strip()
    if action == "processes":
        return _top_processes()
    if action == "storage":
        return _storage()
    if action == "startup":
        return _startup()
    if action != "overview":
        raise RuntimeError("Доступны действия: overview, processes, storage, startup.")
    vm = psutil.virtual_memory()
    disks = _storage()
    try:
        c = psutil.disk_usage(str(Path.home().anchor or os.getcwd()))
        disk_summary = f"Свободно на системном диске: {c.free / 1024**3:.1f} ГБ из {c.total / 1024**3:.1f} ГБ."
    except OSError:
        disk_summary = disks
    return (f"CPU: {psutil.cpu_percent(interval=0.2):.1f}%; RAM: {vm.percent:.1f}% "
            f"({vm.used / 1024**3:.1f}/{vm.total / 1024**3:.1f} ГБ); "
            f"процессов: {len(psutil.pids())}. {disk_summary}\n"
            "Для подробностей вызови system_diagnostics отдельно с action=processes, storage или startup.")


TOOL = {
    "name": "system_diagnostics",
    "description": "Читает состояние компьютера и помогает искать причины тормозов: общая нагрузка CPU/RAM и свободное место, топ процессов по CPU/RAM, диски, записи автозагрузки Windows. Только чтение, не завершает процессы и не отключает автозапуск. Используй как часть диагностики ПК; сначала overview, затем при необходимости processes/storage/startup.",
    "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "overview, processes, storage или startup; по умолчанию overview"}}, "required": []},
    "handler": system_diagnostics,
}
