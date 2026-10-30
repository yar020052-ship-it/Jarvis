from types import SimpleNamespace
import sys

import psutil

from core import cpu_temperature
from actions import system_monitor


def test_prefers_cpu_package_sensor_and_ignores_corrupt_values(monkeypatch):
    monkeypatch.setattr(cpu_temperature.platform, "system", lambda: "Linux")
    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: {
        "coretemp": [
            SimpleNamespace(label="Core 0", current=51.5),
            SimpleNamespace(label="Package id 0", current=64.2),
            SimpleNamespace(label="Core 1", current=999),
        ],
    }, raising=False)
    assert cpu_temperature.read_cpu_temperature() == (64.2, "coretemp")


def test_acpi_zone_is_not_reported_as_cpu_temperature(monkeypatch):
    monkeypatch.setattr(cpu_temperature.platform, "system", lambda: "Linux")
    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: {
        "acpi": [SimpleNamespace(label="", current=47.0)],
        "thermal_zone0": [SimpleNamespace(label="", current=42.0)],
    }, raising=False)
    assert cpu_temperature.read_cpu_temperature() == (None, "N/A")


def test_system_status_surfaces_cpu_and_gpu_telemetry(monkeypatch):
    monkeypatch.setattr(system_monitor.psutil, "cpu_percent", lambda interval: 23.4)
    monkeypatch.setattr(system_monitor.psutil, "virtual_memory", lambda: SimpleNamespace(percent=48.2, used=2 * 1024**3, total=4 * 1024**3))
    monkeypatch.setattr(system_monitor.psutil, "boot_time", lambda: 1000.0)
    monkeypatch.setattr(system_monitor.psutil, "pids", lambda: [1, 2, 3])
    monkeypatch.setattr(system_monitor, "read_cpu_temperature", lambda: (61.5, "coretemp"))
    monkeypatch.setattr(system_monitor, "_get_gpu_temperature", lambda: 58.0)
    monkeypatch.setattr(system_monitor, "_get_gpu_usage", lambda: 36.0)

    status = system_monitor.get_system_status()
    assert status["cpu_temp_c"] == 61.5
    assert status["gpu_temp_c"] == 58.0
    assert status["temperature_c"] == 61.5
    assert status["temperature_kind"] == "CPU"
    assert status["temperature_source"] == "coretemp"
    assert status["gpu_percent"] == 36.0
    assert status["cpu_percent"] == 23.4
    assert status["ram_used_gb"] == 2.0
    assert status["process_count"] == 3


def test_system_status_falls_back_to_gpu_temperature(monkeypatch):
    monkeypatch.setattr(system_monitor.psutil, "cpu_percent", lambda interval: 10.0)
    monkeypatch.setattr(system_monitor.psutil, "virtual_memory", lambda: SimpleNamespace(percent=20, used=1, total=2))
    monkeypatch.setattr(system_monitor.psutil, "boot_time", lambda: 1000.0)
    monkeypatch.setattr(system_monitor.psutil, "pids", lambda: [1])
    monkeypatch.setattr(system_monitor, "read_cpu_temperature", lambda: (None, "N/A"))
    monkeypatch.setattr(system_monitor, "_get_gpu_temperature", lambda: 60.0)
    monkeypatch.setattr(system_monitor, "_get_gpu_usage", lambda: 4.0)

    status = system_monitor.get_system_status()
    assert status["cpu_temp_c"] is None
    assert status["gpu_temp_c"] == 60.0
    assert status["temperature_c"] == 60.0
    assert status["temperature_kind"] == "GPU"
    assert status["temperature_source"] == "NVIDIA GPU · NVML"


def test_nvml_device_is_initialized_once_and_gpu_overheat_is_reported(monkeypatch):
    calls = {"init": 0}

    def init():
        calls["init"] += 1

    fake_nvml = SimpleNamespace(
        NVML_TEMPERATURE_GPU=0,
        nvmlInit=init,
        nvmlDeviceGetHandleByIndex=lambda index: f"GPU{index}",
        nvmlDeviceGetUtilizationRates=lambda _device: SimpleNamespace(gpu=32),
        nvmlDeviceGetTemperature=lambda _device, _sensor: 88,
    )
    monkeypatch.setitem(sys.modules, "pynvml", fake_nvml)
    monkeypatch.setattr(system_monitor, "_nvml_api", None)
    monkeypatch.setattr(system_monitor, "_nvml_device", None)
    monkeypatch.setattr(system_monitor, "_nvml_checked", False)
    assert system_monitor._get_gpu_usage() == 32.0
    assert system_monitor._get_gpu_temperature() == 88.0
    assert calls["init"] == 1

    monkeypatch.setattr(system_monitor.psutil, "cpu_percent", lambda interval: 5.0)
    monkeypatch.setattr(system_monitor.psutil, "virtual_memory", lambda: SimpleNamespace(percent=10))
    monkeypatch.setattr(system_monitor, "_get_cpu_temp", lambda: -1.0)
    monitor = system_monitor.SystemMonitor({"gpu_temp": 80})
    monkeypatch.setattr(system_monitor.time, "monotonic", lambda: 1000.0)
    alert = monitor.check()
    assert "GPU temperature is 88°C" in alert
    assert monitor.check() is None  # the temperature alert obeys its cooldown


def test_resource_history_stores_aggregates_and_reports_recurring_peak(tmp_path, monkeypatch):
    from datetime import datetime

    history = system_monitor.ResourceHistory(tmp_path / "telemetry" / "history.json")
    now = datetime.now().astimezone()
    local_now = datetime.fromtimestamp(now.timestamp())
    sample = {
        "ts": int(now.timestamp()),
        "hour": local_now.hour, "weekday": local_now.weekday(),
        "cpu": 82.0, "ram": 85.0,
        "disk": 55.0, "swap": 10.0, "gpu": None,
    }
    monkeypatch.setattr(system_monitor, "_resource_sample", lambda _now=None: sample)
    assert history.record(force=True)
    assert history.record(force=True)
    assert history.record(force=True)

    stored = history.path.read_text(encoding="utf-8")
    assert "process" not in stored.lower()
    assert "cpu" in stored and "ram" in stored

    summary = history.summary(now=now)
    assert summary["sample_count"] == 3
    assert summary["averages_percent"]["cpu"] == 82.0
    assert summary["recurring_high_load"] == [{
        "weekday": local_now.weekday(), "hour": local_now.hour, "samples": 3,
        "cpu_percent": 82.0, "ram_percent": 85.0,
    }]
    assert summary["automatic_optimization"] is False


def test_resource_history_prunes_samples_older_than_seven_days(tmp_path):
    import json
    from datetime import datetime

    history = system_monitor.ResourceHistory(tmp_path / "history.json")
    now = datetime.now().astimezone()
    old = int(now.timestamp()) - 8 * 86400
    fresh = int(now.timestamp()) - 60
    history.path.parent.mkdir(parents=True, exist_ok=True)
    history.path.write_text(json.dumps({"version": 1, "samples": [
        {"ts": old, "cpu": 95, "ram": 95, "weekday": 0, "hour": 0},
        {"ts": fresh, "cpu": 25, "ram": 40, "weekday": 2, "hour": 11},
    ]}), encoding="utf-8")

    summary = history.summary(now=now)
    assert summary["sample_count"] == 1
    assert summary["averages_percent"]["cpu"] == 25.0
