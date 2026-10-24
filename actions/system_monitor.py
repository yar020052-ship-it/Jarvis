"""
System Monitor — background metric checks with voice alert support.
Zero subprocess calls on all platforms — uses ctypes/pynvml/psutil/wmi only.
"""

import ctypes
import json
import platform
import threading
import time
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from tempfile import NamedTemporaryFile
from typing import Any

import psutil
from core.cpu_temperature import read_cpu_temperature
from core.paths import get_user_data_dir

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu": 90.0,
    "ram": 90.0,
    "temp": 85.0,
    "gpu": 95.0,
    "gpu_temp": 85.0,
}

_COOLDOWN = 300
_CPU_STREAK = 3
_HISTORY_DAYS = 7
_SAMPLE_INTERVAL = 60
_MAX_SAMPLES = _HISTORY_DAYS * 24 * 60
_HISTORY_PATH = get_user_data_dir() / "telemetry" / "resource_history.json"
_HISTORY_LOCK = threading.Lock()

# ── NVML DLL cache (Windows: nvml.dll, Linux: libnvidia-ml.so.1) ─────────────
_nvml_lib: object = None
_nvml_ok: object = None  # None=untested  True=works  False=unavailable
_nvml_api: object = None
_nvml_device: object = None
_nvml_checked = False
_nvml_lock = threading.Lock()


def _nvml_device_handle():
    """Initialize the Python NVML binding once and share its first GPU handle."""
    global _nvml_api, _nvml_device, _nvml_checked
    with _nvml_lock:
        if not _nvml_checked:
            _nvml_checked = True
            try:
                import pynvml  # type: ignore

                pynvml.nvmlInit()
                _nvml_api = pynvml
                _nvml_device = pynvml.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                _nvml_api = None
                _nvml_device = None
    if _nvml_api is None or _nvml_device is None:
        return None, None
    return _nvml_api, _nvml_device


def _nvml_gpu() -> float:
    """GPU utilisation via NVML — zero subprocess on all platforms."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            if _OS == "Windows":
                candidates = ("nvml", r"C:\Windows\System32\nvml.dll")
                _load = ctypes.WinDLL
            else:
                candidates = (
                    "libnvidia-ml.so.1",
                    "libnvidia-ml.so",
                    "libnvidia-ml.dylib",
                )
                _load = ctypes.CDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            return -1.0

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        u = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        _nvml_ok = True
        return float(u.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


def _get_gpu_usage() -> float:
    # nvidia-ml-py exposes the established pynvml import; its handle is reused
    # because system_status and the background monitor can poll concurrently.
    pynvml, device = _nvml_device_handle()
    if pynvml is not None:
        try:
            return float(pynvml.nvmlDeviceGetUtilizationRates(device).gpu)
        except Exception:
            pass

    return _nvml_gpu()


def _get_gpu_temperature() -> float | None:
    """Return the first NVIDIA GPU's temperature via NVML, if it is available."""
    pynvml, device = _nvml_device_handle()
    if pynvml is None:
        return None
    try:
        sensor = getattr(pynvml, "NVML_TEMPERATURE_GPU", 0)
        temperature = float(pynvml.nvmlDeviceGetTemperature(device, sensor))
        return temperature if 0.0 < temperature <= 150.0 else None
    except Exception:
        return None


def _get_cpu_temp() -> float:
    value, _source = read_cpu_temperature()
    return value if value is not None else -1.0


def get_system_status() -> dict:
    """Snapshot of current system metrics for the system_status tool."""
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    cpu_temp, cpu_source = read_cpu_temperature()
    cpu_temp = cpu_temp if cpu_temp is not None and cpu_temp > 0 else None
    gpu_temp = _get_gpu_temperature()
    temperature = cpu_temp if cpu_temp is not None else gpu_temp
    temperature_kind = "CPU" if cpu_temp is not None else "GPU" if gpu_temp is not None else None
    temperature_source = cpu_source if cpu_temp is not None else "NVIDIA GPU · NVML" if gpu_temp is not None else None
    gpu = _get_gpu_usage()

    boot_time = psutil.boot_time()
    uptime_secs = time.time() - boot_time
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)

    return {
        "cpu_percent": round(cpu, 1),
        "ram_percent": round(ram.percent, 1),
        "ram_used_gb": round(ram.used / 1024**3, 1),
        "ram_total_gb": round(ram.total / 1024**3, 1),
        "cpu_temp_c": round(cpu_temp, 1) if cpu_temp is not None else None,
        "gpu_temp_c": round(gpu_temp, 1) if gpu_temp is not None else None,
        "temperature_c": round(temperature, 1) if temperature is not None else None,
        "temperature_kind": temperature_kind,
        "temperature_source": temperature_source,
        "gpu_percent": round(gpu, 1) if gpu >= 0 else None,
        "uptime": f"{uptime_h}h {uptime_m}m",
        "process_count": len(psutil.pids()),
    }


def _resource_sample(now: datetime | None = None) -> dict[str, Any]:
    """Collect a small, privacy-preserving system resource sample."""
    now = now or datetime.now().astimezone()
    ram = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(str(Path.home().anchor or "/"))
        disk_percent = round(float(disk.percent), 1)
    except Exception:
        disk_percent = None
    try:
        swap_percent = round(float(psutil.swap_memory().percent), 1)
    except Exception:
        swap_percent = None
    gpu = _get_gpu_usage()
    return {
        "ts": int(now.timestamp()),
        "hour": datetime.fromtimestamp(now.timestamp()).hour,
        "weekday": datetime.fromtimestamp(now.timestamp()).weekday(),
        "cpu": round(float(psutil.cpu_percent(interval=None)), 1),
        "ram": round(float(ram.percent), 1),
        "disk": disk_percent,
        "swap": swap_percent,
        "gpu": round(gpu, 1) if gpu >= 0 else None,
    }


class ResourceHistory:
    """Append compact one-minute aggregates to a local rolling seven-day log."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else _HISTORY_PATH
        self._last_sample = 0.0
        self._sample_lock = threading.Lock()

    def _read(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("samples"), list):
                cutoff = int(time.time()) - _HISTORY_DAYS * 86400
                return [
                    item for item in raw["samples"]
                    if isinstance(item, dict)
                    and isinstance(item.get("ts"), (int, float))
                    and item["ts"] >= cutoff
                ][-_MAX_SAMPLES:]
        except (OSError, ValueError, TypeError):
            pass
        return []

    def record(self, *, force: bool = False, now: datetime | None = None) -> bool:
        """Store a sample at most once per minute; return whether it was stored."""
        with self._sample_lock:
            current = time.monotonic()
            if not force and current - self._last_sample < _SAMPLE_INTERVAL:
                return False
            self._last_sample = current

        try:
            sample = _resource_sample(now)
            with _HISTORY_LOCK:
                samples = self._read()
                samples.append(sample)
                samples = samples[-_MAX_SAMPLES:]
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.path.parent,
                    prefix="resource_history_", suffix=".tmp", delete=False,
                ) as handle:
                    json.dump({"version": 1, "samples": samples}, handle,
                              separators=(",", ":"))
                    temp_path = Path(handle.name)
                try:
                    os.replace(temp_path, self.path)
                finally:
                    temp_path.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Summarize resource trends and recurring hourly usage patterns."""
        now = now or datetime.now().astimezone()
        cutoff = int(now.timestamp()) - _HISTORY_DAYS * 86400
        with _HISTORY_LOCK:
            samples = [s for s in self._read() if s["ts"] >= cutoff]

        metrics = ("cpu", "ram", "disk", "swap", "gpu")
        averages = {
            key: round(mean(values), 1) if values else None
            for key in metrics
            if (values := [float(s[key]) for s in samples if s.get(key) is not None])
        }
        hourly: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for sample in samples:
            try:
                weekday = int(sample.get("weekday", 0))
                hour = int(sample.get("hour", 0))
                if 0 <= weekday <= 6 and 0 <= hour <= 23:
                    hourly[(weekday, hour)].append(sample)
            except (TypeError, ValueError):
                continue
        recurring = []
        for (weekday, hour), rows in sorted(hourly.items()):
            if len(rows) < 3:
                continue
            cpu_values = [float(r.get("cpu")) for r in rows if r.get("cpu") is not None]
            ram_values = [float(r.get("ram")) for r in rows if r.get("ram") is not None]
            if not cpu_values or not ram_values:
                continue
            cpu = mean(cpu_values)
            ram = mean(ram_values)
            if cpu >= 75 or ram >= 80:
                recurring.append({
                    "weekday": weekday,
                    "hour": hour,
                    "samples": len(rows),
                    "cpu_percent": round(cpu, 1),
                    "ram_percent": round(ram, 1),
                })
        recurring.sort(key=lambda x: max(x["cpu_percent"] / 75, x["ram_percent"] / 80), reverse=True)

        advice = []
        if len(samples) < 60:
            advice.append("Нужно накопить не менее часа выборок, чтобы оценить привычный профиль нагрузки.")
        if averages.get("cpu", 0) >= 70:
            advice.append("Средняя загрузка CPU высокая; проверьте, какие задачи регулярно выполняются в фоне.")
        if averages.get("ram", 0) >= 80:
            advice.append("Память часто загружена; проверьте автозапуск и крупные приложения.")
        if averages.get("disk") is not None and averages["disk"] >= 85:
            advice.append("На системном диске мало свободного места; проверьте крупные файлы и кэш.")
        if recurring:
            advice.append("Есть повторяющиеся часы высокой нагрузки; планируйте тяжёлые задачи вне этих периодов.")
        if not advice:
            advice.append("По накопленным данным устойчивых признаков перегрузки пока нет.")
        return {
            "sample_count": len(samples),
            "window_days": _HISTORY_DAYS,
            "sample_interval_seconds": _SAMPLE_INTERVAL,
            "oldest_sample": samples[0]["ts"] if samples else None,
            "averages_percent": averages,
            "recurring_high_load": recurring[:8],
            "recommendations": advice,
            "storage": str(self.path),
            "automatic_optimization": False,
            "metrics_collected": ["CPU", "RAM", "system_disk", "swap", "GPU_if_available"],
            "privacy": "Local aggregates only; no process names, file names, network destinations, or content.",
        }


_resource_history = ResourceHistory()


def get_resource_usage_insights() -> dict[str, Any]:
    """Return a safe read-only resource trend summary."""
    return _resource_history.summary()


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a [SYSTEM_ALERT] string or None.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> str | None:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            temp = _get_cpu_temp()
            gpu = _get_gpu_usage()
            gpu_temp = _get_gpu_temperature()
        except Exception:
            return None

        alerts: list[str] = []

        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in Russian and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in Russian and suggest freeing memory."
            )
            self._record("ram")

        if temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in Russian and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in Russian."
            )
            self._record("gpu")

        if (
            gpu_temp is not None
            and gpu_temp >= self.thresholds["gpu_temp"]
            and self._can_alert("gpu_temp")
        ):
            alerts.append(
                f"[SYSTEM_ALERT] GPU temperature is {gpu_temp:.0f}°C — above the safe limit. "
                "Warn the user in Russian and suggest reducing graphics load or checking cooling."
            )
            self._record("gpu_temp")

        return " ".join(alerts) if alerts else None
