"""Route JARVIS's own voice to an Android device's speaker, on request.

JARVIS normally speaks through the computer's speakers. This action lets the
owner ask for the reply to come out of their Android tablet/phone instead:

    "включи звук на планшете"      → android_speaker(action="enable")
    "говори через планшет"          → android_speaker(action="enable")
    "верни голос на компьютер"      → android_speaker(action="disable")
    "громче на планшете"            → android_speaker(action="volume", volume=100)

The transport (a tiny DEX player run through ``app_process`` plus an
``adb forward`` TCP bridge) lives in ``core/voice_router``; ``main.py`` writes
every synthesised PCM chunk to it while the route is on. Nothing is installed
on the device — only a few kilobytes of bytecode are pushed to
``/data/local/tmp``.

This is strictly the owner's own, ADB-authorized device, exactly like the rest
of android_control: it does not bypass any logins, prompts or consents.
"""

from __future__ import annotations

import math
import re
import struct

from core.voice_router import get_router
from memory.config_manager import get_android_voice_device, save_android_voice_device


def _resolve_target(device_id: str = "") -> str:
    """Turn a device_id, a friendly name (``RT2``, ``Redmi``) or nothing into a serial.

    With nothing given, prefer the saved voice device, then the single connected
    device. Several connected devices is an error the model should resolve by
    asking — the same rule android_control uses.
    """
    from actions.android_control import (
        _devices,
        _find_connected,
        _same_physical,
        _select_device,
    )

    requested = (device_id or "").strip()
    if requested:
        # A serial in either form (mDNS name or ip:port) — also reconnects a
        # wireless device whose port changed.
        found = _find_connected(requested)
        if found:
            return found
        # Otherwise treat the string as a friendly name/model fragment.
        frag = requested.lower()
        matches: list[str] = []
        for serial, detail in _devices():
            if frag in serial.lower() or frag in detail.lower():
                if not any(_same_physical(serial, kept) for kept in matches):
                    matches.append(serial)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"Под «{requested}» подходит несколько устройств: {', '.join(matches)}. "
                "Уточни device_id."
            )
        # Let the shared selector try to wake/reconnect it and give its usual
        # precise error message if it really is gone.
        return _select_device(requested)

    saved = get_android_voice_device()
    if saved:
        found = _find_connected(saved)
        if found:
            return found
        try:
            return _select_device(saved)
        except RuntimeError:
            pass

    unique: list[str] = []
    for serial, _detail in _devices():
        if not any(_same_physical(serial, kept) for kept in unique):
            unique.append(serial)
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return _select_device("")
    raise RuntimeError(
        "Подключено несколько Android-устройств: "
        + ", ".join(unique)
        + ". Скажи, на какое выводить звук, например «на планшет RT2»."
    )


def _set_media_volume(serial: str, percent: int) -> str:
    """Set the device media volume; the index range is discovered, not assumed."""
    from actions.android_control import _run

    percent = max(0, min(100, int(percent)))
    current = _run(
        ["shell", "cmd", "media_session", "volume", "--stream", "3", "--get"],
        serial,
        timeout=10,
    )
    match = re.search(r"range \[0\.\.(\d+)\]", current)
    top = int(match.group(1)) if match else 0
    if top <= 0:
        # Several builds print nothing for `--get`, so the old fallback assumed a
        # maximum of 15. On this Redmi the real maximum is 25, which meant 90%
        # became 14/25 = 56% - loud on paper but easy to miss across a room.
        # Read the true maximum from the audio dump before setting the index.
        dump = _run(["shell", "dumpsys", "audio"], serial, timeout=10)
        block = re.search(r"STREAM_MUSIC:.*?Max:\s*(\d+)", dump, re.S)
        top = int(block.group(1)) if block else 15
    index = int(round(percent / 100.0 * top))
    _run(
        ["shell", "cmd", "media_session", "volume", "--stream", "3", "--set", str(index)],
        serial,
        timeout=10,
    )
    return f"Громкость медиа на устройстве — {percent}% ({index}/{top})."


def _tone_pcm(seconds: float = 1.0, freq: float = 660.0, rate: int = 24000) -> bytes:
    """A short, soft test tone (16-bit mono PCM) for the route self-test."""
    out = bytearray()
    total = int(rate * seconds)
    for i in range(total):
        # Gentle fade in/out so the beep is not a click.
        env = min(1.0, i / (rate * 0.02), (total - i) / (rate * 0.05))
        sample = int(0.22 * env * 32767 * math.sin(2 * math.pi * freq * i / rate))
        out += struct.pack("<h", sample)
    return bytes(out)


def android_speaker(parameters=None, **_ctx) -> str:
    p = parameters or {}
    action = str(p.get("action", "status")).strip().lower()
    router = get_router()

    if action == "status":
        base = router.status()
        from actions.android_control import _devices

        connected = [serial for serial, _ in _devices()]
        if connected:
            base += " Подключённые устройства: " + ", ".join(connected) + "."
        return base

    if action == "enable":
        serial = _resolve_target(str(p.get("device_id", "")))
        mode = str(p.get("mode", "both")).strip().lower()
        try:
            rate = int(p.get("rate") or 0) or 24000
        except (TypeError, ValueError):
            rate = 24000
        message = router.enable(serial, rate=rate, mode=mode)
        save_android_voice_device(serial)
        try:
            percent = 80 if p.get("volume") is None else int(p.get("volume"))
            message += " " + _set_media_volume(serial, percent)
        except Exception as exc:  # volume is cosmetic — never fail the route
            message += f" (громкость не изменена: {exc})"
        return message

    if action == "disable":
        return router.disable()

    if action == "volume":
        serial = router.serial or _resolve_target(str(p.get("device_id", "")))
        try:
            percent = int(p.get("volume", 80))
        except (TypeError, ValueError):
            raise RuntimeError("Укажи volume от 0 до 100.")
        return _set_media_volume(serial, percent)

    if action == "test":
        if not router.enabled:
            return "Сначала включи вывод звука на устройство (action=enable)."
        sent = router.write(_tone_pcm())
        return (
            "Тестовый сигнал отправлен на устройство — должен быть слышен короткий гудок."
            if sent
            else "Маршрут звука на устройство оборвался — включи заново (action=enable)."
        )

    raise RuntimeError("action должен быть: status, enable, disable, volume или test.")


TOOL = {
    "name": "android_speaker",
    "description": (
        "Направляет собственный голос JARVIS на динамик Android-устройства владельца по его просьбе "
        "(например «включи звук на планшете», «говори через планшет», «транслируй голос на телефон») "
        "и выключает это («верни голос на компьютер»). После enable все реплики JARVIS звучат через динамик "
        "выбранного устройства; звук передаётся в реальном времени, ничего на устройстве не устанавливается. "
        "Работает только с авторизованными по ADB устройствами самого владельца. "
        "action=enable (device_id — serial или понятное имя вроде RT2/Redmi, volume 0-100, "
        "mode=both — на ПК и устройство, mode=device — только на устройство), "
        "action=disable, action=status, action=volume (volume 0-100), action=test (короткий гудок для проверки). "
        "Если подключено несколько устройств и неясно, какое выбрать, сначала спроси владельца."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status, enable (выводить голос на устройство), disable (вернуть голос на компьютер), volume (задать громкость), test (проверка гудком)",
            },
            "device_id": {
                "type": "STRING",
                "description": "ADB serial или понятное имя устройства (например RT2, Redmi, OUKITEL); необязательно при одном устройстве",
            },
            "volume": {
                "type": "INTEGER",
                "description": "Громкость медиа устройства в процентах 0-100 (для enable/volume)",
            },
            "mode": {
                "type": "STRING",
                "description": "both — звук и на компьютере, и на устройстве (по умолчанию); device — только на устройстве",
            },
            "rate": {
                "type": "INTEGER",
                "description": "Частота дискретизации PCM; обычно не указывать (по умолчанию 24000)",
            },
        },
        "required": ["action"],
    },
    "handler": android_speaker,
}
