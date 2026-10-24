"""Read an actual CPU temperature sensor without mistaking ACPI zones for CPU."""

from __future__ import annotations

import platform
from typing import Optional


_CPU_SENSOR_NAMES = (
    "coretemp",  # Intel DTS
    "k10temp",   # AMD CPU package / cores
    "zenpower",  # AMD CPU package / cores
    "cpu_thermal",
    "cpu-thermal",
    "cpu_thermal_zone",
)
_CPU_LABEL_WORDS = ("cpu", "core", "package", "tctl", "tdie", "ccd")
_GENERIC_SENSOR_NAMES = {"thermal", "wmi thermal zone"}


def _is_generic_sensor(name: object) -> bool:
    folded = str(name).casefold()
    return (
        folded in _GENERIC_SENSOR_NAMES
        or folded.startswith("acpi")
        or folded.startswith("thermal_zone")
    )


def _valid_temperature(value: object) -> Optional[float]:
    try:
        temperature = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    # Reject uninitialised, disconnected, or clearly corrupt sensor readings.
    return temperature if -20.0 <= temperature <= 150.0 else None


def read_cpu_temperature() -> tuple[Optional[float], str]:
    """Return (CPU °C, sensor name), or (None, 'N/A') if CPU telemetry is absent.

    Windows' MSAcpi_ThermalZoneTemperature reports motherboard/chassis ACPI
    zones on many machines. It is deliberately not used as a CPU temperature
    fallback; only vendor CPU sensor names or explicitly CPU-labelled readings
    are accepted.
    """
    try:
        import psutil

        sensors = psutil.sensors_temperatures()
    except (AttributeError, NotImplementedError, OSError, RuntimeError):
        sensors = {}
    except Exception:
        sensors = {}

    if isinstance(sensors, dict):
        # Known CPU backends are authoritative; prefer package/die over cores.
        for wanted in _CPU_SENSOR_NAMES:
            for name, rows in sensors.items():
                if str(name).casefold() != wanted.casefold() or not rows:
                    continue
                ranked = sorted(
                    rows,
                    key=lambda row: (
                        0 if any(tag in str(getattr(row, "label", "")).casefold()
                                 for tag in ("package", "tctl", "tdie")) else 1
                    ),
                )
                for row in ranked:
                    value = _valid_temperature(getattr(row, "current", None))
                    if value is not None:
                        return value, str(name)

        # Other OS-specific backends may expose a CPU explicitly in the label.
        for name, rows in sensors.items():
            if _is_generic_sensor(name) or not rows:
                continue
            for row in rows:
                label = str(getattr(row, "label", "")).casefold()
                if any(word in label for word in _CPU_LABEL_WORDS):
                    value = _valid_temperature(getattr(row, "current", None))
                    if value is not None:
                        return value, f"{name}: {getattr(row, 'label', '').strip()}".strip(": ")

    if platform.system() == "Windows":
        # HardwareMonitor-compatible providers expose CPU/package/core sensor
        # names under one of these namespaces. ACPI thermal-zone values are
        # intentionally excluded: they are not evidence of die temperature.
        for namespace in (r"root\LibreHardwareMonitor", r"root\OpenHardwareMonitor"):
            try:
                import wmi  # type: ignore

                rows = wmi.WMI(namespace=namespace).Sensor(Where="SensorType='Temperature'")
                candidates = []
                for row in rows:
                    name = str(getattr(row, "Name", "")).casefold()
                    identifier = str(getattr(row, "Identifier", "")).casefold()
                    parent = str(getattr(row, "Parent", "")).casefold()
                    identity = " ".join((name, identifier, parent))
                    if not any(word in identity for word in _CPU_LABEL_WORDS):
                        continue
                    value = _valid_temperature(getattr(row, "Value", None))
                    if value is not None:
                        rank = 0 if any(x in name for x in ("package", "tctl", "tdie")) else 1
                        candidates.append((rank, value, str(getattr(row, "Name", "CPU sensor"))))
                if candidates:
                    _, value, source = min(candidates)
                    return value, f"{namespace.rsplit(chr(92), 1)[-1]}: {source}"
            except Exception:
                continue

    return None, "N/A"
