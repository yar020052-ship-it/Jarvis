# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller single-exe для JARVIS (Windows).

Сборка:
    pip install pyinstaller
    python -m PyInstaller jarvis.spec --noconfirm
Результат: dist/Jarvis.exe (windowed, без консоли).

Что включено:
- main.py как entry, hidden-imports для actions/plugins автозагрузки
- datas: assets/, config/*.json примеры, core/face_model.obj, core/promt.txt,
  greeting.txt — всё, что читается по относительному пути в рантайме
- Qt: PyQt6 собирается автоматически; QtMultimedia плагины — через collect_all

Заметь: core/paths.py уже поддерживает frozen (sys.executable parent),
поэтому user-data (config/memory/logs) пишется в %LOCALAPPDATA%\\JARVIS,
а не внутрь exe.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Тяжёлые пакеты с native DLL — собираем всё целиком
_datas, _binaries, _hidden = [], [], []
for pkg in ("PyQt6", "sounddevice", "numpy", "cv2", "psutil", "mss", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        _datas += d
        _binaries += b
        _hidden += h
    except Exception:
        pass

hiddenimports = sorted(set(_hidden + collect_submodules("actions") + collect_submodules("plugins") + [
    "win32com", "win32com.client", "wmi", "comtypes", "pycaw", "pywinauto",
    "winotify", "pygetwindow", "google.genai", "telethon", "mcp",
    "fastapi", "uvicorn", "cryptography", "qrcode",
    "sounddevice", "numpy", "psutil", "requests", "bs4",
    "core.window_tracker", "actions.window_snapshot",
]))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=_binaries,
    datas=_datas + [
        ("assets", "assets"),
        ("core/face_model.obj", "core"),
        ("core/promt.txt", "core"),
        ("greeting.txt", "."),
        ("config/mcp_servers.json", "config"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pytest", "IPython"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed — консоль не показывать
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico" if __import__("pathlib").Path("assets/icon.ico").exists() else None,
)
