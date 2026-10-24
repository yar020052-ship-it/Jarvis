"""Read-only Wi-Fi and network diagnostics for the local computer."""

from __future__ import annotations

import platform
import re
import shutil
import socket
import subprocess


def _run(args: list[str], timeout: int = 8) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Недоступно: {exc}"
    output = (result.stdout or result.stderr).strip()
    return output if output else (f"Команда завершилась с кодом {result.returncode}." if result.returncode else "Нет данных.")


def _windows_report() -> str:
    netsh = shutil.which("netsh") or r"C:\Windows\System32\netsh.exe"
    interfaces = _run([netsh, "wlan", "show", "interfaces"])
    drivers = _run([netsh, "wlan", "show", "drivers"])
    ipconfig = _run([shutil.which("ipconfig") or r"C:\Windows\System32\ipconfig.exe", "/all"])
    # Keep diagnostics useful without dumping MAC addresses, host names, or saved profiles.
    wanted = re.compile(r"(Name|Description|State|SSID|Radio type|Channel|Signal|Receive rate|Transmit rate|Authentication|Cipher|Driver|IPv4 Address|Default Gateway|DNS Servers|DHCP Enabled|Media State|Имя(?: интерфейса)?|Описание|Состояние|Тип радиосвязи|Тип радио|Канал|Сигнал|Скорость приема|Скорость передачи|Проверка подлинности|Шифр|Поставщик|Версия драйвера|IPv4-адрес|Основной шлюз|DNS-серверы|DHCP включен|Состояние среды)\s*[:.]", re.I)
    lines = []
    for section, value in (("Wi-Fi интерфейс", interfaces), ("Драйвер Wi-Fi", drivers), ("Сеть и IP", ipconfig)):
        selected = [line.strip() for line in value.splitlines() if wanted.search(line)]
        lines.append(f"{section}:\n" + ("\n".join(selected[:45]) if selected else "Не удалось извлечь краткие поля из вывода Windows."))
    return "\n\n".join(lines)


def _generic_report() -> str:
    details = []
    if shutil.which("nmcli"):
        details.append("Подключение:\n" + _run(["nmcli", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show"]))
        details.append("Wi-Fi:\n" + _run(["nmcli", "-f", "IN-USE,SSID,SIGNAL,CHAN,SECURITY", "device", "wifi", "list", "--rescan", "no"]))
    elif shutil.which("networksetup"):
        details.append(_run(["networksetup", "-getairportnetwork", "en0"]))
        details.append(_run(["networksetup", "-getinfo", "Wi-Fi"]))
    else:
        raise RuntimeError("Для диагностики Wi-Fi на этой системе не найден системный сетевой инструмент.")
    return "\n\n".join(details)


def _connectivity() -> str:
    results = []
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            results.append("Маршрут в интернет (TCP/443): доступен")
    except OSError as exc:
        results.append(f"Маршрут в интернет (TCP/443): нет соединения ({exc.__class__.__name__})")
    try:
        socket.getaddrinfo("example.com", 443)
        results.append("Разрешение DNS: работает")
    except OSError as exc:
        results.append(f"Разрешение DNS: ошибка ({exc.__class__.__name__})")
    return "\n".join(results)


def wifi_diagnostics(parameters=None, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "full")).lower().strip()
    if action == "connectivity":
        return _connectivity()
    if action not in {"full", "wifi", "connectivity"}:
        raise RuntimeError("Доступны действия: full, wifi, connectivity.")
    report = _windows_report() if platform.system() == "Windows" else _generic_report()
    if action == "full":
        report += "\n\nПроверка связи:\n" + _connectivity()
    return report


TOOL = {
    "name": "wifi_diagnostics",
    "description": "Проводит read-only диагностику Wi-Fi и сети: точка доступа, уровень сигнала, канал, драйвер, IP, шлюз, DNS и доступность внешней сети. Используй для вопросов 'почему не работает Wi-Fi/интернет', диагностики сети и проверки беспроводного соединения. Ничего не меняет и не отключает. action=full для полной проверки, wifi для состояния адаптера, connectivity для проверки связи/DNS.",
    "parameters": {
        "type": "OBJECT",
        "properties": {"action": {"type": "STRING", "description": "full, wifi или connectivity; по умолчанию full"}},
        "required": [],
    },
    "handler": wifi_diagnostics,
}
