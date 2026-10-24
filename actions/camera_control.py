"""Enumerate and select local cameras by DirectShow name or capture index."""

from __future__ import annotations

import platform


def _names() -> list[str]:
    if platform.system() != "Windows":
        return []
    try:
        from pygrabber.dshow_graph import FilterGraph

        return list(FilterGraph().get_input_devices())
    except Exception:
        return []


def camera_control(parameters=None, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "list")).lower().strip()
    names = _names()
    from actions.screen_processor import _load_config, _save_config_key
    selected = _load_config().get("camera_index")

    if action in {"list", "status"}:
        if names:
            lines = []
            for index, label in enumerate(names):
                # DirectShow enumeration and OpenCV CAP_DSHOW share device order.
                # Enumeration reads metadata only; it does not open a video stream.
                marker = " — выбрана" if str(selected) == str(index) else ""
                lines.append(f"{label} [индекс {index}]{marker}")
            return "Камеры Windows (список устройств; кадр не открывался):\n" + "\n".join(lines)
        if platform.system() == "Windows":
            return "Не удалось получить имена камер Windows. Установите pygrabber и перезапустите JARVIS. Видео не открывалось."
        if platform.system() == "Linux":
            import glob
            paths = sorted(glob.glob("/dev/video*"))
            return "Камеры: " + (", ".join(paths) if paths else "видеоустройства не найдены")
        return "Перечисление имён камер поддерживается сейчас через DirectShow на Windows. Видео не открывалось."

    if action != "select":
        raise RuntimeError("Доступны действия: list, status, select.")
    wanted = p.get("name")
    index_value = p.get("index")
    if index_value is not None:
        try:
            index = int(index_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Индекс камеры должен быть целым числом из списка.") from exc
    elif wanted:
        matches = [i for i, name in enumerate(names) if str(wanted).casefold() in name.casefold()]
        if len(matches) != 1:
            raise RuntimeError("Имя не соответствует ровно одной камере. Сначала вызовите action=list и передайте точное уникальное имя либо индекс.")
        index = matches[0]
    else:
        raise RuntimeError("Для select укажите name или index.")
    if index < 0 or (names and index >= len(names)):
        raise RuntimeError("Индекс камеры отсутствует в списке устройств. Сначала вызовите action=list.")
    _save_config_key("camera_index", index)
    label = names[index] if index < len(names) else f"камера {index}"
    return f"Выбрана камера {label} [индекс {index}]. Следующий screen_process с angle=camera возьмёт кадр с неё."


TOOL = {
    "name": "camera_control",
    "description": "Перечисляет доступные локальные веб-камеры и выбирает активную для screen_process/open_camera. В Windows показывает Friendly Name DirectShow, например имя USB-камеры. Сначала action=list, затем action=select с уникальной частью name или индексом из списка. Открытие камеры делается только по явной просьбе пользователя.",
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "list, status или select"},
        "name": {"type": "STRING", "description": "Уникальное имя камеры или его часть для select"},
        "index": {"type": "INTEGER", "description": "Индекс камеры из list для select"},
    }, "required": []},
    "handler": camera_control,
}
