"""Слушать микрофон Android-устройства владельца, только по его просьбе.

Зачем: JARVIS уже умеет говорить через динамик планшета (android_speaker)
и получать микрофон из фирменного приложения (ws/phone-audio), но у
владельца есть и обычные телефоны по ADB без этого приложения. Этот экшен
забирает звук обычным scrcpy (ничего на устройство не ставится):

    «послушай микрофон» / «что там слышно» → phone_mic(action="listen_live")
    «хватит слушать» / «выключи прослушку»  → phone_mic(action="stop")
    «слушается ли что-то»                   → phone_mic(action="status")
    «запиши 10 секунд с микрофона»          → phone_mic(action="record", seconds=10)

Правила (строго):
- Микрофон включается ТОЛЬКО по прямой просьбе владельца. Двусмысленная
  фраза («проверь телефон», «что там») — сначала уточнить вслух, потом звать.
- При нескольких подключённых устройствах неясно какое — спросить, а не гадать.
- Источник output (звук самого телефона: музыка, видео) scrcpy отдаёт только
  с Android 13; на Android 11-12 работает только микрофон — так и отвечать.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PID_FILE = BASE_DIR / "logs" / "phone_mic.pid"

SCRCPY_MIN = (2, 1)
MIC_SOURCE = "mic-voice-communication"  # с шумоподавлением, лучше для речи


def _scrcpy() -> str:
    exe = shutil.which("scrcpy")
    if not exe:
        raise RuntimeError("scrcpy не найден в PATH — поставь scrcpy 2.1+.")
    return exe


def _sdk(serial: str) -> int:
    from actions.android_control import _run

    out = _run(["shell", "getprop", "ro.build.version.sdk"], serial, timeout=8)
    try:
        return int((out or "").strip().split()[0])
    except (ValueError, IndexError):
        return 0


def _android_rel(sdk: int) -> str:
    return {30: "11", 31: "12", 32: "12L", 33: "13", 34: "14", 35: "15"}.get(
        sdk, f"API {sdk}" if sdk else "?"
    )


def _resolve(requested: str = "") -> str:
    """Serial: явный > единственный. Несколько — ошибка с просьбой уточнить."""
    from actions.android_control import (
        _devices,
        _find_connected,
        _same_physical,
        _select_device,
    )

    requested = (requested or "").strip()
    if requested:
        found = _find_connected(requested)
        if found:
            return found
        return _select_device(requested)
    try:
        return _select_device("")
    except Exception:
        pass
    unique: list[str] = []
    for serial, _detail in _devices():
        if "._adb-tls-connect." in serial or "._adb." in serial:
            continue
        if not any(_same_physical(serial, kept) for kept in unique):
            unique.append(serial)
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return _select_device("")
    raise RuntimeError(
        "Подключено несколько Android-устройств: "
        + ", ".join(unique)
        + ". Скажи, какое слушать (device_id), например «микрофон Redmi»."
    )


def _pid_alive(pid: int) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, timeout=10,
        ).stdout.decode("cp866", "replace")
        return "INFO:" not in out and str(pid) in out
    except Exception:
        return False


def _live_meta() -> dict | None:
    try:
        meta = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    alive = [p for p in meta.get("pids", []) if _pid_alive(p)]
    if not alive:
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        return None
    meta["pids"] = alive
    return meta


def _wake(serial: str) -> None:
    from actions.android_control import _run

    try:
        _run(["shell", "input", "keyevent", "224"], serial, timeout=5)
    except Exception:
        pass


def _save_meta(pids: list[int], sources: list[str], serial: str) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(
        json.dumps({"pids": pids, "sources": sources, "serial": serial}),
        encoding="utf-8",
    )


def phone_mic(parameters=None, **_ctx) -> str:
    p = parameters or {}
    action = str(p.get("action", "status")).strip().lower()

    if action == "status":
        from actions.android_control import _devices

        devs = [
            s for s, _ in _devices()
            if "._adb-tls-connect." not in s and "._adb." not in s
        ]
        live = _live_meta()
        base = (
            "Подключённые устройства: " + ", ".join(devs) + "."
            if devs
            else "Телефоны по ADB не на связи."
        )
        if live:
            base += (
                f" Сейчас слушается {live.get('serial')} "
                f"({', '.join(live.get('sources', []))})."
            )
        else:
            base += " Прослушка не идёт."
        return base

    if action == "stop":
        live = _live_meta()
        if not live:
            return "Прослушка и так не идёт."
        import signal

        for pid in live["pids"]:
            try:
                import os

                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(1.0)
        for pid in live["pids"]:
            if _pid_alive(pid):
                try:
                    import os

                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        return "Прослушка остановлена."

    if action in ("listen_live", "record"):
        if _live_meta() and action == "listen_live":
            return "Прослушка уже идёт (сначала stop)."
        source = str(p.get("source", "mic") or "mic").strip().lower()
        if source not in ("mic", "output"):
            raise RuntimeError("source должен быть mic или output.")
        serial = _resolve(str(p.get("device_id", "")))
        sdk = _sdk(serial)
        if source == "output" and sdk and sdk < 33:
            raise RuntimeError(
                f"Звук самого телефона scrcpy отдаёт только с Android 13, "
                f"а здесь Android {_android_rel(sdk)} — с этого устройства "
                f"доступен только микрофон."
            )
        if sdk and sdk < 30:
            raise RuntimeError(
                f"Захват звука по scrcpy нужен Android 11+, здесь {_android_rel(sdk)}."
            )
        exe = _scrcpy()
        src = MIC_SOURCE if source == "mic" else "playback"
        _wake(serial)
        if action == "record":
            try:
                seconds = min(max(1.0, float(p.get("seconds", 10) or 10)), 600.0)
            except (TypeError, ValueError):
                seconds = 10.0
            target = str(
                Path(tempfile.gettempdir())
                / f"jarvis_mic_{time.strftime('%Y%m%d-%H%M%S')}.opus"
            )
            proc = subprocess.Popen(
                [exe, "-s", serial, "--no-video", "--no-window",
                 f"--audio-source={src}", f"--record={target}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + seconds + 20.0
            started = time.time()
            size = 0
            while time.time() < deadline:
                time.sleep(1.0)
                try:
                    size = Path(target).stat().st_size
                except OSError:
                    size = 0
                if time.time() >= started + seconds and size >= 500:
                    break
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            if size < 500:
                return (
                    "Запись пустая: Android ждёт разрешения на экране телефона "
                    "(«Разрешить запись звука?»), либо микрофон занят. "
                    "Разблокируй экран, разреши и повтори."
                )
            return f"Записал {seconds:.0f} сек с микрофона {serial}: {target} ({size // 1024} КБ)."
        # listen_live — звук в динамики ПК в реальном времени
        cmd = [exe, "-s", serial, "--no-video", "--no-window",
               f"--audio-source={src}"]
        if src == "playback":
            cmd.append("--audio-dup")  # звук остаётся и на телефоне
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(3.0)
        if not _pid_alive(proc.pid):
            raise RuntimeError(
                "scrcpy сразу завершился — проверь, что телефон отвечает по adb."
            )
        _save_meta([proc.pid], [src], serial)
        what = "микрофон" if source == "mic" else "звук телефона"
        return (
            f"Слушаю {what} ({serial}) — звук идёт в динамики ПК "
            f"в реальном времени. Остановка: phone_mic(action=stop)."
        )

    raise RuntimeError(
        "action должен быть: status, listen_live, record, stop."
    )


TOOL = {
    "name": "phone_mic",
    "description": (
        "Слушает микрофон Android-устройства владельца через scrcpy и выводит звук "
        "в динамики ПК в реальном времени (например «послушай микрофон», «что там слышно», "
        "«включи прослушку») или пишет короткий срез в файл. Работает только "
        "с авторизованными по ADB устройствами самого владельца, ничего на устройство "
        "не ставится. СТРОГО: включать ТОЛЬКО по прямой просьбе владельца; двусмысленная "
        "фраза — сначала уточнить вслух. При нескольких устройствах и неясном выборе — "
        "спросить, какое слушать. action=listen_live (source=mic — микрофон комнаты, "
        "source=output — звук самого телефона, только Android 13+; device_id — serial "
        "или имя вроде RT2/Redmi), action=record (seconds — срез микрофона в файл), "
        "action=stop (выключить прослушку), action=status."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status, listen_live (слушать живьём), record (записать срез), stop (остановить)",
            },
            "device_id": {
                "type": "STRING",
                "description": "ADB serial или понятное имя устройства (RT2, Redmi); необязательно при одном устройстве",
            },
            "source": {
                "type": "STRING",
                "description": "mic — микрофон (по умолчанию), output — звук самого телефона (нужен Android 13+)",
            },
            "seconds": {
                "type": "INTEGER",
                "description": "длина среза в секундах для action=record",
            },
        },
        "required": ["action"],
    },
    "handler": phone_mic,
}
