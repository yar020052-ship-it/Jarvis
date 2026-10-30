"""Windows 11 automation helpers for JARVIS.

This module contains no policy of its own: every function performs exactly the
operation it is named for. The caller (actions/windows_automation.py) decides
which operations need the UI confirmation gate. Keeping policy out of this file
makes the helpers testable without a running JARVIS session.

All functions are best-effort and return an ``OperationResult`` instead of
raising, so a missing permission or an unusual Windows edition is reported to
the user rather than crashing the action.
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Any

if platform.system() != "Windows":
    raise RuntimeError("windows_automation is only available on Windows")

_CREATE_NO_WINDOW = 0x08000000


@dataclass
class OperationResult:
    success: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _run_powershell(script: str, timeout: int = 25) -> OperationResult:
    """Run a PowerShell script without opening a console window."""
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return OperationResult(False, "Операция Windows не ответила вовремя.", {"timeout": timeout})
    except OSError as exc:
        return OperationResult(False, f"Не удалось запустить PowerShell: {exc}")

    details = {"stdout": (completed.stdout or "").strip(), "stderr": (completed.stderr or "").strip()}
    if completed.returncode == 0:
        return OperationResult(True, "Готово.", details)
    return OperationResult(False, "Windows вернула ошибку.", details)


def _run_command(args: list[str], timeout: int = 25) -> OperationResult:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return OperationResult(False, "Операция Windows не ответила вовремя.", {"timeout": timeout})
    except OSError as exc:
        return OperationResult(False, f"Не удалось выполнить команду: {exc}")

    details = {"stdout": (completed.stdout or "").strip(), "stderr": (completed.stderr or "").strip()}
    if completed.returncode == 0:
        return OperationResult(True, "Готово.", details)
    return OperationResult(False, "Windows вернула ошибку.", details)


def is_admin() -> bool:
    """True when the current process can change system-wide settings."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def wifi_enabled() -> bool | None:
    """Current Wi-Fi state, or None when the adapter is not present."""
    result = _run_command(["netsh", "interface", "show", "interface", "Wi-Fi"])
    if not result.success:
        return None
    text = str(result.details.get("stdout") or "").lower()
    if "enabled" in text:
        return True
    if "disabled" in text:
        return False
    return None


def set_wifi(enabled: bool) -> OperationResult:
    """Enable or disable the Wi-Fi adapter.

    This is a system-wide change and normally needs administrator rights. The
    caller should ask the user through the UI confirmation gate first.
    """
    if not is_admin():
        return OperationResult(
            False,
            "Для переключения Wi-Fi нужны права администратора.",
            {"admin_required": True},
        )
    state = "enable" if enabled else "disable"
    return _run_command(["netsh", "interface", "set", "interface", "Wi-Fi", f"admin={state}"])


def restart(delay_seconds: int = 0) -> OperationResult:
    """Restart Windows after an optional delay. Destructive: confirm first."""
    delay = max(0, min(600, int(delay_seconds)))
    return _run_command(["shutdown", "/r", "/t", str(delay)])


def shutdown(delay_seconds: int = 0) -> OperationResult:
    """Shut Windows down after an optional delay. Destructive: confirm first."""
    delay = max(0, min(600, int(delay_seconds)))
    return _run_command(["shutdown", "/s", "/t", str(delay)])


def cancel_shutdown() -> OperationResult:
    """Cancel a pending shutdown/restart timer."""
    return _run_command(["shutdown", "/a"])


def lock_workstation() -> OperationResult:
    """Lock the current Windows session."""
    try:
        ok = bool(ctypes.windll.user32.LockWorkStation())
    except Exception as exc:
        return OperationResult(False, f"Не удалось заблокировать экран: {exc}")
    return OperationResult(ok, "Рабочий стол заблокирован." if ok else "Windows не заблокировала экран.")


def open_settings(page: str = "") -> OperationResult:
    """Open a Windows Settings page. Page is a whitelisted keyword, not a URI."""
    pages = {
        "": "",
        "home": "",
        "network": "network",
        "wifi": "network/wifi",
        "display": "display",
        "sound": "sound",
        "bluetooth": "bluetooth",
        "privacy": "privacy",
        "update": "windowsupdate",
        "apps": "appsfeatures",
        "battery": "batterysaver",
    }
    key = str(page or "").strip().lower()
    if key not in pages:
        return OperationResult(False, f"Неизвестная страница настроек: {page}")
    suffix = pages[key]
    try:
        os.startfile(f"ms-settings:{suffix}")  # type: ignore[attr-defined]
    except Exception as exc:
        return OperationResult(False, f"Не удалось открыть настройки: {exc}")
    return OperationResult(True, "Открываю настройки Windows.")


def set_volume(percent: int) -> OperationResult:
    """Set master volume to 0..100 when pycaw is available."""
    try:
        from ctypes import cast, POINTER

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except Exception:
        return OperationResult(
            False,
            "Управление громкостью недоступно: не установлен pycaw.",
            {"dependency": "pycaw"},
        )

    value = max(0, min(100, int(percent)))
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(value / 100.0, None)
    except Exception as exc:
        return OperationResult(False, f"Не удалось изменить громкость: {exc}")
    return OperationResult(True, f"Громкость установлена на {value}%.")


def set_brightness(percent: int) -> OperationResult:
    """Set display brightness when the panel exposes WMI brightness control."""
    value = max(0, min(100, int(percent)))
    script = (
        "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
        "-ErrorAction Stop; "
        f"$m | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={value}}} "
        "| Out-Null"
    )
    result = _run_powershell(script)
    if result.success:
        return OperationResult(True, f"Яркость установлена на {value}%.")
    return OperationResult(
        False,
        "Не удалось изменить яркость. На внешних мониторах WMI-яркость обычно недоступна.",
        result.details,
    )
