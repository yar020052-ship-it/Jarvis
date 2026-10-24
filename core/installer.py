"""
JARVIS — Dependency auto-installer.

Called by the Windows setup flow. Installs missing engine packages and ensures
older NVIDIA GPUs have a compatible CUDA-capable PyTorch build before local
models are loaded.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
from typing import Callable

# ── Package lists ─────────────────────────────────────────────────────────
# Each entry: (import_name, pip_package_name)

_CORE: list[tuple[str, str]] = [
    ("psutil", "psutil"),
    ("PIL", "pillow"),
    ("sounddevice", "sounddevice"),
    ("numpy", "numpy"),
    ("requests", "requests"),
    ("bs4", "beautifulsoup4"),
    ("ddgs", "ddgs"),
    ("pyautogui", "pyautogui"),
    ("pyperclip", "pyperclip"),
    ("pygetwindow", "pygetwindow"),
    ("mss", "mss"),
    ("cv2", "opencv-python"),
    ("soundfile", "soundfile"),
    ("miniaudio", "miniaudio"),
    ("send2trash", "send2trash"),
    ("pptx", "python-pptx"),
    ("youtube_transcript_api", "youtube-transcript-api"),
]

# Windows-only (pywinauto, pycaw, winotify, comtypes)
_WINDOWS: list[tuple[str, str]] = [
    ("comtypes", "comtypes"),
    ("pycaw", "pycaw"),
    ("winotify", "winotify"),
    ("pywinauto", "pywinauto"),
]

# STT engine packages
_STT: dict[str, list[tuple[str, str]]] = {
    "whisper": [("faster_whisper", "faster-whisper")],
    "vosk": [("vosk", "vosk")],
}

# TTS engine packages
_TTS: dict[str, list[tuple[str, str]]] = {
    "edgetts": [("edge_tts", "edge-tts")],
    # kokoro>=0.9 dropped AlbertModel/AutoModel from transformers — version pin is critical
    "kokoro": [("kokoro", "kokoro>=0.9"), ("soundfile", "soundfile")],
    "elevenlabs": [],  # uses only requests, already in core
}


# ── Helpers ───────────────────────────────────────────────────────────────


def _available(module: str) -> bool:
    """Return True if the module can be imported (no actual import)."""
    return importlib.util.find_spec(module) is not None


def _pip(package: str, log: Callable | None = None) -> bool:
    if log:
        log(f"SYS: pip install {package} …")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            package,
            "--quiet",
            "--disable-pip-version-check",
        ],
        capture_output=True,
    )
    ok = result.returncode == 0
    if not ok and log:
        stderr = result.stderr.decode(errors="replace").strip()
        log(f"ERR: {package} install failed — {stderr[:140]}")
    return ok


# PyTorch 2.14 is the last release with prebuilt CUDA wheels for Pascal GPUs
# such as the GTX 1080 Ti. CUDA 12.6 is therefore intentional; newer CUDA 13
# wheels no longer contain kernels for this GPU generation.
_PASCAL_TORCH = "torch==2.14.1+cu126"
_PYTORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu126"


def _nvidia_gpu_info() -> tuple[str, str] | None:
    """Return the first visible NVIDIA GPU name and compute capability."""
    if platform.system() != "Windows":
        return None
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return None
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        name, separator, capability = line.rpartition(",")
        if separator and name.strip() and capability.strip():
            return name.strip(), capability.strip()
    return None


def _torch_cuda_build() -> tuple[str, str] | None:
    """Return the installed Torch CUDA version and compiled architecture list."""
    if not _available("torch"):
        return None
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import torch; print((torch.version.cuda or 'cpu') + '|' + ','.join(torch.cuda.get_arch_list()))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        return None
    line = probe.stdout.strip().splitlines()[-1] if probe.stdout.strip() else ""
    cuda_version, _, arch_list = line.partition("|")
    return cuda_version, arch_list


def _ensure_gpu_torch(log: Callable | None = None) -> None:
    """Install a Pascal-capable CUDA Torch build when an NVIDIA GPU is present."""
    gpu_info = _nvidia_gpu_info()
    if not gpu_info:
        return
    gpu_name, capability = gpu_info
    try:
        major, minor = (int(part) for part in capability.split(".", 1))
    except ValueError:
        return
    # Current PyTorch CUDA wheels target newer cards already; CUDA 12.6 is
    # specifically for Maxwell/Pascal/Volta, whose support has ended in newer
    # wheels. Do not downgrade RTX/Turing-and-newer systems to this build.
    if (major, minor) >= (7, 5):
        return
    target_arch = f"sm_{major}{minor}"

    current = _torch_cuda_build()
    if current and current[0].startswith("12.") and target_arch in current[1].split(","):
        if log:
            log(f"SYS: PyTorch CUDA {current[0]} is ready for {gpu_name}.")
        return

    if log:
        log(
            f"SYS: {gpu_name} detected. Installing the Pascal-compatible "
            "PyTorch CUDA 12.6 build (one-time download)…"
        )
    install_command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        "--index-url",
        _PYTORCH_CUDA_INDEX,
        "--extra-index-url",
        "https://pypi.org/simple",
        _PASCAL_TORCH,
    ]
    if log:
        process = subprocess.Popen(
            install_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            message = line.strip()
            if message:
                log(f"SYS: pip: {message}")
        return_code = process.wait()
    else:
        return_code = subprocess.run(install_command).returncode
    if return_code != 0:
        if log:
            log(f"ERR: CUDA PyTorch install failed (pip exit code {return_code}).")
        return

    installed = _torch_cuda_build()
    if installed and installed[0].startswith("12.") and target_arch in installed[1].split(","):
        if log:
            log(f"SYS: PyTorch CUDA {installed[0]} ready for {gpu_name}.")
    elif log:
        log(f"ERR: PyTorch installed, but this build does not advertise {target_arch} support.")


def ensure_gpu_support(log: Callable | None = None) -> None:
    """Configure a compatible local PyTorch CUDA build when needed."""
    _ensure_gpu_torch(log)


# ── Public API ────────────────────────────────────────────────────────────


def install_for_config(config: dict, log: Callable | None = None) -> None:
    """
    Install all missing packages required by *config*.

    Blocking — always call from a background thread.
    Progress is reported via the optional *log* callback (receives a str).
    """
    stt = config.get("stt_engine", "whisper").lower()
    tts = config.get("tts_engine", "edgetts").lower()

    needed: list[tuple[str, str]] = list(_CORE)
    needed += _STT.get(stt, [])
    needed += _TTS.get(tts, [])
    if platform.system() == "Windows":
        needed += _WINDOWS

    # Deduplicate (preserve order, key = pip name)
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for mod, pkg in needed:
        if pkg not in seen:
            seen.add(pkg)
            unique.append((mod, pkg))

    missing = [(mod, pkg) for mod, pkg in unique if not _available(mod)]

    if not missing:
        if log:
            log("SYS: All dependencies already installed ✓")
        _ensure_gpu_torch(log)
        return

    pkg_names = ", ".join(p for _, p in missing)
    if log:
        log(f"SYS: Installing {len(missing)} package(s): {pkg_names}")

    for _mod, pkg in missing:
        _pip(pkg, log)

    # CPU-only PyTorch wheels silently disable local CUDA inference. Check this
    # after the configured engines are installed, before their models load.
    _ensure_gpu_torch(log)

    # Playwright: install the package + download Chromium browser
    if not _available("playwright"):
        _pip("playwright", log)
        if log:
            log("SYS: Downloading Playwright browser (Chromium, ~150 MB — one-time)…")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
        )
        if log:
            log("SYS: Playwright browser ready.")

    if log:
        log("SYS: All dependencies ready ✓")
