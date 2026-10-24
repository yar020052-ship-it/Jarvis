"""Единый поиск бинарников Android SDK Platform-Tools (adb, fastboot).

Раньше каждый модуль искал adb по-своему:

  - ``android_control._adb_path``  учитывал ``JARVIS_ADB_PATH``, но не умел
    дописывать ``.exe`` и падал сразу;
  - ``android_device._tool``       дописывал ``.exe``, но ИГНОРИРОВАЛ
    ``JARVIS_ADB_PATH`` — пользователь явно указал путь, а часть действий
    всё равно брала первый попавшийся adb из PATH;
  - ``voice_router._adb_bin``      просто перенаправлял на ``_adb_path``.

Две разные логики означали, что одно и то же действие могло сработать в одном
месте и не сработать в другом. Здесь — одна каноническая, с двумя контрактами:
``find_tool`` возвращает ``None``, если не найден, ``require_tool`` бросает
с подсказкой по установке (как это делал ``_adb_path``).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

_HINT = (
    "Установите Android SDK Platform-Tools и добавьте adb в PATH "
    "или задайте переменную JARVIS_ADB_PATH. См. docs/android_setup_ru.md."
)


def find_tool(name: str) -> str | None:
    """Путь к ``name`` (обычно ``adb``) или ``None``.

    Порядок: переменная окружения ``JARVIS_<NAME>_PATH`` (явное указание
    владельца выигрывает у PATH), затем PATH с запасным ``.exe`` для Windows.
    """
    name = (name or "").strip()
    if not name:
        return None
    env = os.environ.get(f"JARVIS_{name.upper()}_PATH", "").strip().strip('"')
    if env:
        if Path(env).is_file():
            return env
        # Явный путь указан, но ведёт в никуда: не подставляем молча чужой adb.
        return None
    return shutil.which(name) or shutil.which(name + ".exe")


def require_tool(name: str) -> str:
    """Как ``find_tool``, но при отсутствии — RuntimeError с подсказкой."""
    exe = find_tool(name)
    if not exe:
        raise RuntimeError(f"Не найден {name}. {_HINT}")
    return exe
