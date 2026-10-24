"""Route JARVIS's spoken audio to an Android device's speaker.

JARVIS synthesises its voice on the PC and writes raw 16-bit PCM to a
sounddevice stream. When the user asks for it, this module *also* streams that
same PCM to an Android device so the reply comes out of the tablet/phone
speaker.

The device side is a few kilobytes of Dalvik bytecode
(``android_speaker/PcmServer.java``, shipped prebuilt as
``assets/jarvis_voice.dex``) run without installing an app. The PC talks to it
**directly over the LAN**; audio never passes through adb:

    adb push assets/jarvis_voice.dex /data/local/tmp/jarvis_voice.dex
    adb shell -T CLASSPATH=/data/local/tmp/jarvis_voice.dex \
        app_process /system/bin com.jarvis.PcmServer --bind 0.0.0.0 --port PORT
    # then the PC opens tcp://<device-lan-ip>:PORT and streams PCM

Why not ``adb forward``? JARVIS constantly drives adb for its Android control
and vision features; whenever that traffic hiccups the helper calls
``adb reconnect``, which tears down *every* forward, and the audio tunnel dies
after a second or two (observed as WSAECONNABORTED). A direct socket between
the PC and the device is unaffected by adb. adb is used only once, to launch
the player.

stdin is deliberately not used: adb.exe on Windows treats 0x1A (Ctrl-Z) as EOF
and silently truncates a binary stream (measured: cut at the first 0x1A).

One persistent connection carries every reply; between replies the AudioTrack
just plays silence, so turning a reply around does not pay a setup cost.
"""

from __future__ import annotations

import atexit
import re
import socket
import subprocess
import threading
import time

_REMOTE_DEX = "/data/local/tmp/jarvis_voice.dex"
_PLAYER_CLASS = "com.jarvis.PcmServer"
_DEFAULT_RATE = 24000
_PORT_RANGE = range(8791, 8803)
_READY_TIMEOUT = 12.0

# Best-effort sweep that stops any player left over from a previous run so the
# chosen port is free. One `grep -l` scans every /proc/<pid>/cmdline at once
# instead of forking a `tr` per pid: on a busy phone the per-pid loop measured
# ~12 s, which dominated every enable/disable; this is ~0.1 s. The class name is
# split across two quotes so this very shell's argv does not contain the literal
# it searches for (the old loop had to special-case its own pid).
_KILL_STALE = (
    "pat='com.jarvis''.PcmServer'; "
    'for f in $(grep -als "$pat" /proc/[0-9]*/cmdline 2>/dev/null); do '
    'p="${f#/proc/}"; kill "${p%/cmdline}" 2>/dev/null; done'
)


class VoiceRouter:
    """Owns the on-device PCM player and the socket JARVIS streams into."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._serial = ""
        self._ip = ""
        self._port = 0
        self._enabled = False
        self._only_device = False

    # ── state ─────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def only_device(self) -> bool:
        with self._lock:
            return self._only_device

    @property
    def serial(self) -> str:
        with self._lock:
            return self._serial

    # ── public API ────────────────────────────────────────────────────────
    def enable(self, serial: str = "", rate: int = _DEFAULT_RATE, mode: str = "both") -> str:
        """Start playing JARVIS's voice on an Android device.

        ``serial`` selects the device (empty = the only/saved one). ``mode`` is
        "both" (PC speaker + device) or "device" (silence the PC speaker while
        the device is playing).
        """
        from actions.android_control import _adb_path, _select_device

        adb = _adb_path()
        with self._lock:
            self._stop_locked()
            serial = _select_device(serial)
            self._push_dex(adb, serial)
            self._kill_stale(adb, serial)
            ip = self._device_ip(adb, serial)

            last_error = ""
            for port in _PORT_RANGE:
                proc = subprocess.Popen(
                    [
                        adb, "-s", serial, "shell", "-T",
                        f"CLASSPATH={_REMOTE_DEX} app_process /system/bin "
                        f"{_PLAYER_CLASS} --bind 0.0.0.0 --port {port} --rate {int(rate)}",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                sock = self._connect(proc, ip, port)
                if sock is not None:
                    self._proc = proc
                    self._sock = sock
                    self._serial = serial
                    self._ip = ip
                    self._port = port
                    self._enabled = True
                    self._only_device = str(mode).strip().lower() in {"device", "android", "only"}
                    break
                code = proc.poll()
                last_error = f"порт {port}" + (f", код {code}" if code is not None else "")
                try:
                    proc.kill()
                except Exception:
                    pass
            else:
                raise RuntimeError(
                    "Не удалось запустить проигрыватель на устройстве ("
                    + (last_error or "нет связи")
                    + "). Убедитесь, что устройство в той же Wi-Fi-сети и на связи по ADB."
                )
        where = "только на устройство" if self._only_device else "на устройство и компьютер"
        return (
            f"Голос JARVIS теперь выводится через динамик устройства {serial} ({where}). "
            "Скажи «выключи звук на планшете», чтобы вернуть голос на компьютер."
        )

    def disable(self) -> str:
        with self._lock:
            was = self._enabled
            serial = self._serial
            self._stop_locked()
        if was:
            return f"Вывод голоса JARVIS на Android-динамик выключен ({serial})."
        return "Вывод голоса JARVIS на Android-динамик и так был выключен."

    def write(self, data: bytes) -> bool:
        """Stream one PCM chunk to the device. Returns False once the route died."""
        if not data:
            return self._enabled
        with self._lock:
            if not self._enabled or self._sock is None:
                return False
            try:
                self._sock.sendall(data)
                return True
            except OSError:
                self._enabled = False
                return False

    def status(self) -> str:
        with self._lock:
            if not self._enabled:
                return "Вывод голоса JARVIS на Android-динамик выключен."
            if self._proc is None or self._proc.poll() is not None or self._sock is None:
                return (
                    "Маршрут звука на Android был включён, но проигрыватель на устройстве "
                    "остановился (возможно, пропала связь по Wi-Fi). Включи заново."
                )
            where = "только на устройство" if self._only_device else "на устройство и компьютер"
            return (
                f"Голос JARVIS выводится {where}, устройство {self._serial} "
                f"({self._ip}:{self._port})."
            )

    # ── internals ─────────────────────────────────────────────────────────
    def _repo_assets(self):
        from pathlib import Path

        return Path(__file__).resolve().parent.parent / "assets" / "jarvis_voice.dex"

    def _push_dex(self, adb: str, serial: str) -> None:
        dex = self._repo_assets()
        if not dex.is_file():
            raise RuntimeError(
                "Не найден проигрыватель assets/jarvis_voice.dex — пересоберите "
                "android_speaker/build.ps1."
            )
        result = subprocess.run(
            [adb, "-s", serial, "push", str(dex), _REMOTE_DEX],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Не удалось загрузить проигрыватель на устройство: "
                + (result.stderr or result.stdout).strip()
            )

    def _kill_stale(self, adb: str, serial: str) -> None:
        try:
            subprocess.run(
                [adb, "-s", serial, "shell", _KILL_STALE],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except Exception:
            pass

    def _device_ip(self, adb: str, serial: str) -> str:
        """The device's LAN address, taken from its adb serial or from ``ip route``."""
        match = re.match(r"(\d{1,3}(?:\.\d{1,3}){3}):\d+$", serial)
        if match:
            return match.group(1)
        for args in (["shell", "ip", "route"], ["shell", "ip", "-o", "addr"]):
            try:
                out = subprocess.run(
                    [adb, "-s", serial, *args],
                    capture_output=True, text=True, timeout=15, check=False,
                ).stdout or ""
            except Exception:
                continue
            found = re.search(r"src (\d{1,3}(?:\.\d{1,3}){3})", out) or re.search(
                r"inet (\d{1,3}(?:\.\d{1,3}){3})/\d+ .*(?:wlan|eth)", out
            )
            if found and not found.group(1).startswith("127."):
                return found.group(1)
        raise RuntimeError(
            f"Не удалось определить Wi-Fi-адрес устройства {serial} для вывода звука."
        )

    def _connect(self, proc: subprocess.Popen, ip: str, port: int) -> socket.socket | None:
        deadline = time.monotonic() + _READY_TIMEOUT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return None
            try:
                sock = socket.create_connection((ip, port), timeout=2)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # A generous ceiling only: real-time pacing means a healthy
                # write returns in a few ms. A 20 s stall means the device is
                # gone, so surfacing OSError (and dropping the route) is right.
                sock.settimeout(20.0)
                return sock
            except OSError:
                time.sleep(0.4)
        return None

    def _stop_locked(self) -> None:
        sock, self._sock = self._sock, None
        proc, self._proc = self._proc, None
        serial, self._serial = self._serial, ""
        self._ip = ""
        self._port = 0
        self._enabled = False
        self._only_device = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if serial:
            try:
                self._kill_stale(_adb_bin(), serial)
            except Exception:
                pass


def _adb_bin() -> str:
    from actions.android_control import _adb_path

    return _adb_path()


_router = VoiceRouter()


def get_router() -> VoiceRouter:
    """The process-wide voice router (one device route at a time)."""
    return _router


def shutdown() -> None:
    """Stop the device route — called on application exit."""
    try:
        _router.disable()
    except Exception:
        pass


atexit.register(shutdown)
