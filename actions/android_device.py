"""Careful Android device access through the official Android platform tools.

This action deliberately exposes a short allowlist of ADB/Fastboot operations.
It never runs arbitrary shell text, unlocks the bootloader, erases partitions,
or flashes without an explicit confirmation in the JARVIS interface.
"""

from __future__ import annotations

import os
import ipaddress
import re
import subprocess
import defusedxml.ElementTree as ET
from pathlib import Path

from core.confirm import request as request_confirmation

_TIMEOUT = 180
_PARTITIONS = {
    "boot", "boot_a", "boot_b", "init_boot", "init_boot_a", "init_boot_b",
    "vendor_boot", "vendor_boot_a", "vendor_boot_b", "recovery", "recovery_a",
    "recovery_b", "dtbo", "dtbo_a", "dtbo_b", "vbmeta", "vbmeta_a",
    "vbmeta_b", "vendor", "vendor_a", "vendor_b", "product", "product_a",
    "product_b", "system", "system_a", "system_b", "system_ext",
    "system_ext_a", "system_ext_b", "odm", "odm_a", "odm_b",
}
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
_ANDROID_KEYS = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recent": "KEYCODE_APP_SWITCH",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "volume_mute": "KEYCODE_VOLUME_MUTE",
    "enter": "KEYCODE_ENTER",
    "delete": "KEYCODE_DEL",
    "space": "KEYCODE_SPACE",
    "tab": "KEYCODE_TAB",
}


def _tool(name: str) -> str | None:
    # Единый резолвер (core/adb.py): учитывает JARVIS_ADB_PATH и дописывает
    # .exe. Раньше здесь был только shutil.which — переменная окружения
    # игнорировалась, и часть действий брала adb не из того места.
    from core.adb import find_tool

    return find_tool(name)


def _run(binary: str, args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    exe = _tool(binary)
    if not exe:
        raise RuntimeError(f"Не найден {binary}. Установите Android SDK Platform-Tools.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run(
        [exe, *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, creationflags=flags, check=False,
    )


def _output(result: subprocess.CompletedProcess) -> str:
    return (result.stdout or result.stderr or "").strip()


# `adb devices` / `fastboot devices` print "<serial> <state>". A serial may contain
# a space (adb renames a duplicate mDNS advert to "<name> (2)"), so split on the
# first known state token instead of on every whitespace run.
_TOOL_STATES = (
    "device", "offline", "unauthorized", "bootloader", "sideload", "recovery",
    "rescue", "host", "connecting", "authorizing", "no permissions",
    "fastboot", "fastbootd",
)
_DEVICE_LINE_RE = re.compile(
    r"^(?P<serial>\S.*?)\s+(?P<state>" + "|".join(_TOOL_STATES) + r")\b(?P<detail>.*)$"
)


def _device_rows(tool: str) -> list[tuple[str, str]]:
    result = _run(tool, ["devices"])
    if result.returncode:
        raise RuntimeError(_output(result) or f"{tool} завершился с ошибкой.")
    rows = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("List"):
            continue
        match = _DEVICE_LINE_RE.match(line)
        if match:
            rows.append((match.group("serial").strip(), match.group("state")))
    return rows


def _select(rows: list[tuple[str, str]], serial: str, mode: str) -> str:
    usable = [sid for sid, state in rows if state == mode]
    if serial:
        if serial not in usable:
            raise RuntimeError(f"Устройство {serial} не найдено в режиме {mode}.")
        return serial
    if not usable:
        raise RuntimeError(
            f"Нет Android-устройства в режиме {mode}. Подключите USB или выполните "
            "сопряжение через Беспроводную отладку и подключение по Wi-Fi."
        )
    if len(usable) > 1:
        raise RuntimeError("Подключено несколько устройств. Укажите serial из списка устройств.")
    return usable[0]


def _status() -> str:
    adb_rows = _device_rows("adb")
    fastboot_rows = _device_rows("fastboot")
    lines = ["Подключённые Android-устройства:"]
    if not adb_rows and not fastboot_rows:
        return "Android-устройство не обнаружено. Подключите его USB или используйте Wi-Fi: Android 11+ поддерживает сопряжение через Настройки → Для разработчиков → Беспроводная отладка."
    for sid, state in adb_rows:
        suffix = ""
        if state == "device":
            for prop, label in (("ro.product.manufacturer", "производитель"),
                                ("ro.product.model", "модель"),
                                ("ro.build.version.release", "Android")):
                value = _output(_run("adb", ["-s", sid, "shell", "getprop", prop], timeout=10))
                if value:
                    suffix += f"; {label}: {value}"
        lines.append(f"ADB: {sid} — {state}{suffix}")
    for sid, state in fastboot_rows:
        suffix = ""
        if state == "fastboot":
            product = _output(_run("fastboot", ["-s", sid, "getvar", "product"], timeout=10))
            if product:
                suffix = f"; {product}"
        lines.append(f"Fastboot: {sid} — {state}{suffix}")
    return "\n".join(lines)


def _flash(serial: str, partition: str, image: str) -> str:
    path = Path(image).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError("Файл образа не найден.")
    if path.suffix.lower() != ".img":
        raise RuntimeError("Fastboot flash принимает только файл образа .img. Для официального OTA используйте sideload.")
    rows = _device_rows("fastboot")
    chosen = _select(rows, serial, "fastboot")
    result = _run("fastboot", ["-s", chosen, "flash", partition, str(path)], timeout=_TIMEOUT)
    if result.returncode:
        raise RuntimeError(_output(result) or "Fastboot сообщил об ошибке при прошивке.")
    return f"Раздел {partition} прошит из {path.name}. Ответ Fastboot: {_output(result)}"


def _sideload(serial: str, package: str) -> str:
    path = Path(package).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise RuntimeError("Укажите существующий ZIP-пакет официального OTA-обновления.")
    chosen = _select(_device_rows("adb"), serial, "sideload")
    result = _run("adb", ["-s", chosen, "sideload", str(path)], timeout=900)
    if result.returncode:
        raise RuntimeError(_output(result) or "ADB sideload сообщил об ошибке.")
    return f"Передача OTA-пакета {path.name} завершилась. Проверьте результат на экране recovery телефона."


def _adb_device(serial: str) -> str:
    return _select(_device_rows("adb"), serial, "device")


def _wifi_endpoint(host: str, port: object) -> str:
    host = str(host or "").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise RuntimeError("Введите локальный IPv4-адрес планшета из раздела «Беспроводная отладка».") from exc
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
        raise RuntimeError("Разрешён только частный IPv4-адрес планшета в локальной сети.")
    try:
        number = int(port)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Укажите порт, который Android показывает в настройках беспроводной отладки.") from exc
    if not 1 <= number <= 65535:
        raise RuntimeError("Порт должен быть в диапазоне от 1 до 65535.")
    return f"{address.compressed}:{number}"


def _pair_wifi(host: str, port: object, pairing_code: str) -> str:
    endpoint = _wifi_endpoint(host, port)
    code = str(pairing_code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise RuntimeError("Введите временный шестизначный код, который сейчас отображается на планшете.")
    result = _run("adb", ["pair", endpoint, code], timeout=30)
    message = _output(result).replace(code, "[скрыто]")
    if result.returncode:
        raise RuntimeError(message or "ADB не смог выполнить сопряжение. Проверьте IP, порт, код и одну Wi-Fi-сеть.")
    return (
        f"Беспроводное сопряжение Android выполнено ({host}). Теперь в главном экране "
        "«Беспроводная отладка» возьмите текущий IP-адрес и основной порт (не порт сопряжения), "
        "затем подключите устройство по Wi-Fi. Код сопряжения скрыт и не сохранён. "
        f"Ответ ADB: {message}"
    )


def _connect_wifi(host: str, port: object) -> str:
    endpoint = _wifi_endpoint(host, port)
    result = _run("adb", ["connect", endpoint], timeout=25)
    message = _output(result)
    if result.returncode or not re.search(r"connected to|already connected to", message, re.I):
        raise RuntimeError(message or "ADB не смог подключиться. Убедитесь, что беспроводная отладка включена и порт актуален.")
    rows = _device_rows("adb")
    state = next((state for sid, state in rows if sid == endpoint), "unknown")
    if state != "device":
        raise RuntimeError(f"ADB ответил «{message}», но устройство пока не готово (состояние: {state}). Проверьте запрос разрешения отладки на экране планшета.")
    return f"Android подключён по Wi-Fi: {endpoint}. JARVIS может управлять им, пока оба устройства в одной сети и беспроводная отладка включена."


def _disconnect_wifi(host: str, port: object) -> str:
    endpoint = _wifi_endpoint(host, port)
    result = _run("adb", ["disconnect", endpoint], timeout=20)
    if result.returncode:
        raise RuntimeError(_output(result) or "ADB не смог отключить устройство.")
    return f"Wi-Fi ADB-соединение с {endpoint} закрыто."


def _adb_output(serial: str, args: list[str], timeout: int = 20) -> str:
    result = _run("adb", ["-s", serial, *args], timeout=timeout)
    if result.returncode:
        raise RuntimeError(_output(result) or "ADB сообщил об ошибке.")
    return result.stdout.strip()


def _android_inspect(serial: str) -> str:
    remote = "/sdcard/window_dump.xml"
    _adb_output(serial, ["shell", "uiautomator", "dump", remote], timeout=35)
    xml = _adb_output(serial, ["exec-out", "cat", remote], timeout=15)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RuntimeError("Android вернул некорректное дерево интерфейса.") from exc
    rows = []
    for node in root.iter("node"):
        label = (node.attrib.get("text") or node.attrib.get("content-desc") or
                 node.attrib.get("resource-id") or "").strip()
        clickable = node.attrib.get("clickable") == "true"
        bounds = node.attrib.get("bounds", "")
        if label or clickable:
            rows.append(f"{'[нажимаемый] ' if clickable else ''}{label[:100] or '(без подписи)'} {bounds}".strip())
        if len(rows) == 100:
            break
    return "Элементы экрана (bounds задают границы элемента):\n" + ("\n".join(rows) if rows else "На экране нет подписанных элементов.")


def _android_screenshot(serial: str) -> str:
    exe = _tool("adb")
    if not exe:
        raise RuntimeError("Не найден adb.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    result = subprocess.run(
        [exe, "-s", serial, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=30, creationflags=flags, check=False,
    )
    if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Не удалось получить снимок экрана Android.")
    path = Path.home() / "Pictures" / "JARVIS" / "android_screen.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.stdout)
    return f"Снимок экрана сохранён: {path}"


def _android_security_check(serial: str) -> str:
    """Collect read-only indicators relevant to a possible Android miner."""
    cpu = _adb_output(serial, ["shell", "top", "-b", "-n", "1", "-m", "18"], timeout=25)
    accessibility = _adb_output(serial, ["shell", "settings", "get", "secure",
                                         "enabled_accessibility_services"], timeout=10)
    listeners = _adb_output(serial, ["shell", "settings", "get", "secure",
                                     "enabled_notification_listeners"], timeout=10)
    policy = _adb_output(serial, ["shell", "dumpsys", "device_policy"], timeout=20)
    packages = _adb_output(serial, ["shell", "pm", "list", "packages", "-3", "-i"], timeout=20)

    lines = ["Проверка Android (только чтение):"]
    # `top` is a short snapshot, not a malware verdict. Keep a few useful rows.
    cpu_lines = [line.strip() for line in cpu.splitlines()
                 if re.match(r"^\s*\d+\s+\S+\s+[-\d]+\s+[-\d]+\s+\S+\s+\S+\s+\S+\s+[A-Z]\s+(?!0(?:\.0+)?\s)\d+(?:\.\d+)?\s+", line)
                 and not re.search(r"\[[^\]]+\]$", line)
                 and not re.search(r"\btop\s+-b\s+-n\s+1", line)]
    if cpu_lines:
        lines.append("Процессы из одного снимка CPU (не доказывает майнинг):")
        lines.extend(f"  {line[:180]}" for line in cpu_lines[:7])
    else:
        lines.append("Список top не удалось разобрать; снимок CPU неполный.")

    def services(title: str, value: str) -> None:
        entries = [entry.strip() for entry in value.split(":") if entry.strip() and entry.strip().lower() != "null"]
        lines.append(title + ("\n" + "\n".join(f"  {entry}" for entry in entries[:12]) if entries else " нет"))

    services("Включённые службы специальных возможностей:", accessibility)
    services("Слушатели уведомлений:", listeners)
    admin_match = re.search(r"Enabled Device Admins.*?(?=\n\s*mPasswordOwner|\Z)", policy, re.S)
    admin_block = admin_match.group(0) if admin_match else ""
    admin_rows = [line.strip() for line in admin_block.splitlines()
                  if re.search(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+/", line)]
    lines.append("Активные администраторы устройства:" +
                 ("\n" + "\n".join(f"  {row}" for row in admin_rows[:12]) if admin_rows else " нет"))

    # Installer metadata can be absent after restore/ADB installation and is not proof of malware.
    other_sources = []
    for row in packages.splitlines():
        match = re.match(r"package:([^\s]+)(?:\s+installer=(.*))?", row.strip())
        if not match:
            continue
        package, installer = match.group(1), (match.group(2) or "").strip()
        if installer != "com.android.vending":
            other_sources.append(f"{package} (installer: {installer or 'не указан'})")
    lines.append("Сторонние приложения без отметки Play Store:" +
                 ("\n" + "\n".join(f"  {entry}" for entry in other_sources[:15]) if other_sources else " нет"))
    if len(other_sources) > 15:
        lines.append(f"  …и ещё {len(other_sources) - 15}.")
    lines.append("Вывод: это первичная проверка, а не антивирусное заключение. Нагрузка CPU — один короткий снимок; неизвестный источник установки и специальные разрешения сами по себе не означают заражение.")
    return "\n".join(lines)


def _android_brightness(serial: str) -> str:
    level = _adb_output(serial, ["shell", "settings", "get", "system", "screen_brightness"])
    mode = _adb_output(serial, ["shell", "settings", "get", "system", "screen_brightness_mode"])
    try:
        percent = round(max(0, min(255, int(level))) * 100 / 255)
    except (TypeError, ValueError):
        raise RuntimeError("Android не сообщил текущую яркость экрана.")
    mode_text = "автояркость включена" if mode == "1" else "ручная яркость"
    return f"Яркость экрана: {percent}% (значение {level}/255; {mode_text})."


def _set_android_brightness(serial: str, value: object) -> str:
    try:
        percent = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Яркость задаётся целым числом от 0 до 100%.") from exc
    if not 0 <= percent <= 100:
        raise RuntimeError("Яркость должна быть от 0 до 100%.")
    level = round(percent * 255 / 100)
    _adb_output(serial, ["shell", "settings", "put", "system", "screen_brightness_mode", "0"])
    _adb_output(serial, ["shell", "settings", "put", "system", "screen_brightness", str(level)])
    actual = _adb_output(serial, ["shell", "settings", "get", "system", "screen_brightness"])
    if actual != str(level):
        raise RuntimeError(f"Android не подтвердил яркость: запрошено {level}/255, получено {actual}.")
    return f"Яркость экрана установлена на {percent}% ({level}/255). Автояркость выключена; включить её можно командой автояркости."


_SETTING_LABELS = {
    "wifi": "Wi-Fi",
    "bluetooth": "Bluetooth",
    "airplane_mode": "режим полёта",
    "location": "геолокация",
    "do_not_disturb": "Не беспокоить",
    "auto_rotate": "автоповорот экрана",
    "auto_brightness": "автояркость",
}


def _android_setting_state(serial: str, name: str) -> bool:
    if name == "wifi":
        output = _adb_output(serial, ["shell", "dumpsys", "wifi"], timeout=20)
        match = re.search(r"(?im)^\s*Wi-Fi is (enabled|disabled)\s*$", output)
        if not match:
            match = re.search(r"(?im)^\s*Current wifi mode: (EnabledState|DisabledState)\s*$", output)
            if match:
                return match.group(1) == "EnabledState"
            raise RuntimeError("Android не сообщил состояние Wi-Fi.")
        return match.group(1).lower() == "enabled"
    if name == "bluetooth":
        output = _adb_output(serial, ["shell", "dumpsys", "bluetooth_manager"], timeout=20)
        match = re.search(r"(?im)^\s*enabled:\s*(true|false)\s*$", output)
        if not match:
            raise RuntimeError("Android не сообщил состояние Bluetooth.")
        return match.group(1).lower() == "true"
    if name == "airplane_mode":
        value = _adb_output(serial, ["shell", "settings", "get", "global", "airplane_mode_on"])
        return value == "1"
    if name == "location":
        value = _adb_output(serial, ["shell", "cmd", "location", "is-location-enabled"])
        match = re.search(r"\b(true|false)\b", value, re.I)
        if not match:
            raise RuntimeError("Android не сообщил состояние геолокации.")
        return match.group(1).lower() == "true"
    if name == "do_not_disturb":
        value = _adb_output(serial, ["shell", "settings", "get", "global", "zen_mode"])
        try:
            return int(value) != 0
        except ValueError as exc:
            raise RuntimeError("Android не сообщил состояние режима «Не беспокоить».") from exc
    key = "accelerometer_rotation" if name == "auto_rotate" else "screen_brightness_mode"
    value = _adb_output(serial, ["shell", "settings", "get", "system", key])
    if value not in {"0", "1"}:
        raise RuntimeError(f"Android не сообщил состояние настройки {key}.")
    return value == "1"


def _set_android_setting(serial: str, name: str, value: object) -> str:
    if name not in _SETTING_LABELS:
        raise RuntimeError("Настройка недоступна. Поддерживаются: " + ", ".join(_SETTING_LABELS) + ".")
    if isinstance(value, bool):
        enabled = value
    elif str(value).strip().lower() in {"on", "true", "1", "вкл", "включить", "включи"}:
        enabled = True
    elif str(value).strip().lower() in {"off", "false", "0", "выкл", "выключить", "выключи"}:
        enabled = False
    else:
        raise RuntimeError("Укажите value как on/off или true/false.")

    state = "true" if enabled else "false"
    if name == "wifi":
        _adb_output(serial, ["shell", "svc", "wifi", "enable" if enabled else "disable"])
    elif name == "bluetooth":
        _adb_output(serial, ["shell", "svc", "bluetooth", "enable" if enabled else "disable"])
    elif name == "airplane_mode":
        _adb_output(serial, ["shell", "cmd", "connectivity", "airplane-mode", "enable" if enabled else "disable"])
    elif name == "location":
        _adb_output(serial, ["shell", "cmd", "location", "set-location-enabled", state])
    elif name == "do_not_disturb":
        _adb_output(serial, ["shell", "cmd", "notification", "set_dnd", "on" if enabled else "off"])
    else:
        key = "accelerometer_rotation" if name == "auto_rotate" else "screen_brightness_mode"
        _adb_output(serial, ["shell", "settings", "put", "system", key, "1" if enabled else "0"])

    actual = _android_setting_state(serial, name)
    if actual != enabled:
        raise RuntimeError(f"Android не подтвердил изменение: {_SETTING_LABELS[name]} осталось {'включено' if actual else 'выключено'}.")
    return f"Параметр «{_SETTING_LABELS[name]}»: {'включено' if enabled else 'выключено'}; состояние проверено."


def android_device(parameters: dict, **_kwargs) -> str:
    action = str(parameters.get("action") or "status").strip().lower()
    serial = str(parameters.get("serial") or "").strip()

    try:
        if action == "pair_wifi":
            return _pair_wifi(str(parameters.get("host") or ""), parameters.get("pair_port"), parameters.get("pairing_code"))

        if action == "connect_wifi":
            return _connect_wifi(str(parameters.get("host") or ""), parameters.get("port"))

        if action == "disconnect_wifi":
            return _disconnect_wifi(str(parameters.get("host") or ""), parameters.get("port"))

        if action in {"status", "devices", "info"}:
            return _status()

        if action in {"screen_on", "screen_off", "volume_up", "volume_down", "volume_mute"}:
            sid = _adb_device(serial)
            key = {
                "screen_on": "KEYCODE_WAKEUP",
                "screen_off": "KEYCODE_SLEEP",
                "volume_up": "KEYCODE_VOLUME_UP",
                "volume_down": "KEYCODE_VOLUME_DOWN",
                "volume_mute": "KEYCODE_VOLUME_MUTE",
            }[action]
            _adb_output(sid, ["shell", "input", "keyevent", key])
            if action == "screen_on":
                return (
                    "Экран планшета разбужен. Если планшет заблокирован и пароль сохранён, "
                    "разузнай его через android_control action=unlock; иначе владелец разблокирует вручную."
                )
            labels = {
                "screen_on": "Экран включён",
                "screen_off": "Экран выключен",
                "volume_up": "Громкость увеличена на один шаг",
                "volume_down": "Громкость уменьшена на один шаг",
                "volume_mute": "Переключено отключение звука",
            }
            return labels[action] + "."

        if action == "get_brightness":
            return _android_brightness(_adb_device(serial))

        if action == "set_brightness":
            return _set_android_brightness(_adb_device(serial), parameters.get("value"))

        if action == "auto_brightness":
            sid = _adb_device(serial)
            _adb_output(sid, ["shell", "settings", "put", "system", "screen_brightness_mode", "1"])
            return "Автояркость включена."

        if action == "get_settings":
            sid = _adb_device(serial)
            rows = [f"{label}: {'включено' if _android_setting_state(sid, name) else 'выключено'}"
                    for name, label in _SETTING_LABELS.items()]
            rows.append(_android_brightness(sid))
            return "Системные настройки планшета:\n" + "\n".join(rows)

        if action == "set_setting":
            return _set_android_setting(_adb_device(serial), str(parameters.get("setting") or "").strip().lower(), parameters.get("value"))

        # App and screen-control requests must never be interpreted as reboot
        # requests. The reboot actions below remain available only for a direct
        # user instruction to restart the device.
        if action in {"open_app", "inspect_screen", "tap", "swipe", "type_text", "press_key", "screenshot", "security_check"}:
            sid = _adb_device(serial)
            if action == "security_check":
                return _android_security_check(sid)
            if action == "open_app":
                package = str(parameters.get("package") or "").strip()
                if not _PACKAGE_RE.fullmatch(package):
                    raise RuntimeError("Укажите package приложения, например com.android.settings.")
                output = _adb_output(sid, ["shell", "monkey", "-p", package,
                                           "-c", "android.intent.category.LAUNCHER", "1"])
                return f"Открытие {package} отправлено Android. {output[-250:]}".strip()

            if action == "inspect_screen":
                return _android_inspect(sid)

            if action == "tap":
                x, y = int(parameters.get("x", -1)), int(parameters.get("y", -1))
                if not (0 <= x <= 10000 and 0 <= y <= 10000):
                    raise RuntimeError("Координаты касания должны быть от 0 до 10000.")
                _adb_output(sid, ["shell", "input", "tap", str(x), str(y)])
                return f"Касание Android выполнено: ({x}, {y})."

            if action == "swipe":
                coords = [int(parameters.get(key, -1)) for key in ("x1", "y1", "x2", "y2")]
                if any(value < 0 or value > 10000 for value in coords):
                    raise RuntimeError("Координаты свайпа должны быть от 0 до 10000.")
                duration = int(parameters.get("duration_ms", 350))
                if not 50 <= duration <= 5000:
                    raise RuntimeError("Длительность свайпа должна быть от 50 до 5000 мс.")
                _adb_output(sid, ["shell", "input", "swipe", *map(str, coords), str(duration)])
                return "Свайп на Android выполнен."

            if action == "type_text":
                text = str(parameters.get("text") or "")
                if not text or len(text) > 2000:
                    raise RuntimeError("Текст должен содержать от 1 до 2000 символов.")
                if not text.isascii() or not re.fullmatch(r"[A-Za-z0-9 .,!?@#%+=:_/-]+", text):
                    raise RuntimeError("Разрешены латинские буквы, цифры, пробел и простая пунктуация.")
                encoded = text.replace("%", "%25").replace(" ", "%s")
                _adb_output(sid, ["shell", "input", "text", encoded])
                return f"Текст введён ({len(text)} символов)."

            if action == "press_key":
                key = str(parameters.get("key") or "").strip().lower()
                if key not in _ANDROID_KEYS:
                    raise RuntimeError(f"Кнопка недоступна. Разрешены: {', '.join(_ANDROID_KEYS)}.")
                _adb_output(sid, ["shell", "input", "keyevent", _ANDROID_KEYS[key]])
                return f"Нажата кнопка {_ANDROID_KEYS[key]} на Android."

            return _android_screenshot(sid)

        if action in {"reboot", "reboot_bootloader", "reboot_recovery"}:
            target = {"reboot": "system", "reboot_bootloader": "bootloader",
                      "reboot_recovery": "recovery"}[action]
            sid = _select(_device_rows("adb"), serial, "device")
            title = f"Перезагрузить телефон {sid} в режим {target}"
            return request_confirmation(
                "android_" + action, title,
                "JARVIS отправит команду ADB. Незавершённые операции на телефоне могут прерваться.",
                lambda: _output(_run("adb", ["-s", sid, "reboot", target], timeout=30))
                or f"Команда перезагрузки в режим {target} отправлена.",
            )

        if action == "fastboot_reboot":
            sid = _select(_device_rows("fastboot"), serial, "fastboot")
            return request_confirmation(
                "android_fastboot_reboot", f"Перезагрузить телефон {sid}",
                "JARVIS отправит команду Fastboot reboot.",
                lambda: _output(_run("fastboot", ["-s", sid, "reboot"], timeout=30))
                or "Команда перезагрузки отправлена.",
            )

        if action == "flash":
            partition = str(parameters.get("partition") or "").strip().lower()
            image = str(parameters.get("image") or "").strip()
            if partition not in _PARTITIONS:
                return "Прошивка отменена: разрешён только известный раздел Android; bootloader, radio, userdata и metadata заблокированы."
            path = Path(image).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() != ".img":
                return "Прошивка отменена: укажите существующий файл образа .img."
            sid = _select(_device_rows("fastboot"), serial, "fastboot")
            title = f"Прошить раздел {partition} телефона {sid}"
            detail = (f"Файл: {path}\nОперация может стереть данные или сделать телефон неработоспособным. "
                      "Сверьте модель и образ с официальной инструкцией производителя.")
            return request_confirmation(
                "android_flash_" + partition, title, detail,
                lambda: _flash(sid, partition, str(path)),
            )

        if action == "sideload_ota":
            package = str(parameters.get("image") or "").strip()
            path = Path(package).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() != ".zip":
                return "Передача OTA отменена: укажите существующий ZIP-пакет обновления."
            sid = _select(_device_rows("adb"), serial, "sideload")
            return request_confirmation(
                "android_sideload_ota", f"Установить OTA-пакет на телефон {sid}",
                f"Файл: {path}\nТелефон должен находиться в recovery и показывать режим ADB sideload.",
                lambda: _sideload(sid, str(path)),
            )

        return "Неизвестное действие Android. Доступны status, pair_wifi, connect_wifi, disconnect_wifi, security_check, open_app, inspect_screen, tap, swipe, type_text, press_key, screen_on, screen_off, volume_up, volume_down, volume_mute, get_brightness, set_brightness, auto_brightness, get_settings, set_setting, screenshot и явно запрошенные операции reboot/flash/sideload."
    except subprocess.TimeoutExpired:
        return "Операция Android превысила допустимое время ожидания. Проверьте состояние устройства и USB/Wi-Fi ADB-подключение."
    except Exception as exc:
        return f"Не удалось выполнить действие Android: {exc}"


TOOL = {
    "name": "android_device",
    "description": (
        "Controls Android devices over ADB, either USB or Wireless debugging on the same local Wi-Fi network. Use status to identify connected devices. "
        "Android 11+ can be paired without USB: when asked to connect a device wirelessly, explain that the user must enable Developer options > Wireless debugging on the device and provide its displayed local IP, temporary pairing port and six-digit pairing code; call pair_wifi with host, pair_port and pairing_code. Never request or repeat the code unless pairing is requested. Then ask for the current main Wireless debugging port (different from the pairing port) and call connect_wifi with host and port. The user must accept any Android debugging prompt. For later sessions, use status; the paired device may auto-connect on the same Wi-Fi. Use disconnect_wifi only when explicitly asked. Limit wireless endpoints to private local IPv4 addresses. "
        "For any request to open Android Settings, use action=open_app with package com.android.settings. "
        "For another app, use open_app when its package is known; otherwise inspect the launcher and tap its visible app label rather than guessing. NEVER use reboot to open an app, "
        "settings, or respond to a vague request. Only use reboot when the user explicitly asks to "
        "restart/reboot the Android device. For requests to check for Android miners, malware indicators, suspicious background activity, or security settings, use action=security_check. "
        "This is read-only: it samples top CPU processes, enabled accessibility services, notification listeners, active device admins, and third-party packages whose installer is absent or not Play Store. "
        "Explain that this is a short diagnostic snapshot, not proof of malware; do not uninstall or disable anything without explicit user instruction. "
        "Treat Android UI actions as a general-purpose workflow, not as a fixed task list: for an ordinary goal, inspect the current screen, choose a visible labeled element, tap its bounds, then inspect again; use swipe, type_text, and press_key as needed and verify the final state. To clear the notification shade, use android_control action=clear_notifications — it taps the real «Clear all» button and verifies the result; never tap the shade blindly from guessed coordinates. To LEARN the device and remember where things are (screens, labels, tap-by-label paths), use android_control actions survey_device / survey_screen / tap_element / recall_place / note_place / map_overview — they maintain a per-device UI map that survives restarts. If an app package is unknown, navigate from the visible launcher instead of inventing one. Do not claim success until the resulting screen or command confirms it. Do not run arbitrary shell commands or bypass Android permission or confirmation prompts. Unlocking the owner's authorized tablet is done through android_control action=unlock with the stored password; app logins, SMS/email codes, subscriptions, and consent prompts stay manual. "
        "Hardware controls: screen_on (wake the display and show the Android lock screen; unlocking is done via android_control action=unlock with the saved password), screen_off, volume_up, volume_down, volume_mute, "
        "get_brightness, set_brightness (integer percent 0-100; switches to manual mode), "
        "and auto_brightness. Use get_settings for a read-only summary and set_setting with a specific setting and value on/off for Wi-Fi, Bluetooth, airplane_mode, location, do_not_disturb, auto_rotate, and auto_brightness; verify the returned state. These commands require an explicitly authorized ADB connection. For other settings use Android Settings UI: open_app com.android.settings, inspect_screen, tap visible controls, inspect again, and never claim success without verification. Screen actions: inspect_screen, tap, swipe, type_text, "
        "press_key, screenshot. These actions require an authorized active ADB connection. Supports ADB device info, "
        "ADB reboot to system/bootloader/recovery, Fastboot reboot, Fastboot flashing of an "
        "allowlisted .img partition, and official OTA ZIP transfer using adb sideload. "
        "Every reboot, flash, or sideload requires the user's on-screen confirmation. "
        "Never unlock the bootloader, erase partitions, flash bootloader/radio/userdata, "
        "or invent a firmware path. Ask for the exact phone model and firmware when missing."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status | pair_wifi | connect_wifi | disconnect_wifi | security_check | open_app | inspect_screen | tap | swipe | type_text | press_key | screen_on | screen_off | volume_up | volume_down | volume_mute | get_brightness | set_brightness | auto_brightness | get_settings | set_setting | screenshot | reboot | reboot_bootloader | reboot_recovery | fastboot_reboot | flash | sideload_ota"},
            "serial": {"type": "STRING", "description": "Optional exact device serial when multiple phones are connected"},
            "host": {"type": "STRING", "description": "Tablet's private local IPv4 address for wireless ADB, e.g. 192.168.1.24"},
            "pair_port": {"type": "INTEGER", "description": "Temporary pairing port displayed under Android Wireless debugging > Pair device with pairing code"},
            "pairing_code": {"type": "STRING", "description": "Temporary six-digit Wireless debugging pairing code; use only for pair_wifi and do not repeat in replies"},
            "port": {"type": "INTEGER", "description": "Current main Wireless debugging port for connect_wifi/disconnect_wifi; this differs from the temporary pairing port"},
            "package": {"type": "STRING", "description": "Android package for open_app, e.g. com.android.settings"},
            "x": {"type": "INTEGER", "description": "Tap x coordinate"},
            "y": {"type": "INTEGER", "description": "Tap y coordinate"},
            "x1": {"type": "INTEGER", "description": "Swipe start x"},
            "y1": {"type": "INTEGER", "description": "Swipe start y"},
            "x2": {"type": "INTEGER", "description": "Swipe end x"},
            "y2": {"type": "INTEGER", "description": "Swipe end y"},
            "duration_ms": {"type": "INTEGER", "description": "Swipe duration from 50 to 5000 milliseconds"},
            "text": {"type": "STRING", "description": "ASCII text to type into the focused Android field"},
            "key": {"type": "STRING", "description": "back, home, recent, volume_up, volume_down, volume_mute, enter, delete, space, or tab"},
            "value": {"type": "STRING", "description": "For set_setting: on/off or true/false. For set_brightness: integer percent 0-100 (for example '60')."},
            "setting": {"type": "STRING", "description": "Allowlisted Android setting: wifi, bluetooth, airplane_mode, location, do_not_disturb, auto_rotate, auto_brightness"},
            "partition": {"type": "STRING", "description": "Allowlisted Fastboot partition name for flash"},
            "image": {"type": "STRING", "description": "Exact local .img path for flash or official OTA .zip path for sideload_ota"},
        },
        "required": [],
    },
    "handler": android_device,
}
