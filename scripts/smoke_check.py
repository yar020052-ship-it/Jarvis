"""Fast, offline contract checks for JARVIS.

Run with ``.venv/Scripts/python.exe scripts/smoke_check.py`` on Windows or
``.venv/bin/python scripts/smoke_check.py`` on macOS/Linux.
"""
import ast
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
fails = []


def check(name, cond):
    print(("OK  " if cond else "FAIL") + f" {name}")
    if not cond:
        fails.append(name)


# Python floor must be the same in the installer and the build metadata.
try:
    setup_tree = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
    setup_min = next(
        ast.literal_eval(node.value)
        for node in setup_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MIN_PY"
                for target in node.targets)
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    match = re.fullmatch(r">=(\d+)\.(\d+)", project["project"]["requires-python"])
    project_min = tuple(map(int, match.groups())) if match else None
    check("installer and project agree on minimum Python", setup_min == project_min)
    check("setup identifies the JARVIS project", "JARVIS needs at least" in (ROOT / "setup.py").read_text(encoding="utf-8"))
    check("setup branding is consistent", "MARK LIV" not in (ROOT / "setup.py").read_text(encoding="utf-8"))
except Exception as exc:
    check(f"Python packaging metadata ({exc})", False)

# GPU telemetry needs NVIDIA's maintained distribution; the code continues to
# import the compatible `pynvml` module exposed by nvidia-ml-py.
try:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check("NVIDIA bindings use nvidia-ml-py", "nvidia-ml-py" in requirements and "nvidia-ml-py" in pyproject_text)
    check("deprecated pynvml distribution is absent", not re.search(r"(?m)^\s*\"?pynvml(?:[<=>\s\"#]|$)", requirements + pyproject_text))
except Exception as exc:
    check(f"GPU dependency metadata ({exc})", False)

# Window tracking remains an isolated, deterministic helper.
try:
    from core.window_tracker import diff_snapshots, summarize

    old = [{"hwnd": 1, "title": "A", "exe": "a.exe", "state": "normal", "active": True}]
    new = [
        {"hwnd": 1, "title": "A", "exe": "a.exe", "state": "minimized", "active": False},
        {"hwnd": 2, "title": "B", "exe": "b.exe", "state": "normal", "active": True},
    ]
    events = diff_snapshots(old, new)
    check("window tracker diff", {(e["type"], e["hwnd"]) for e in events} == {("minimized", 1), ("opened", 2)})
    check("window tracker summary", "2" in summarize(new))
except Exception as exc:
    check(f"window tracker ({exc})", False)

# Discover an actual built-in action through the same loader used at startup.
try:
    from core.action_loader import discover_actions

    registry = discover_actions(actions_dir=ROOT / "actions", reserved_names=set(), logger=lambda _msg: None)
    check("window_snapshot action discovered", registry.has("window_snapshot"))
except Exception as exc:
    check(f"action discovery ({exc})", False)

# Render the current smooth face into a raster image. This catches failures in
# mesh posing, projection, and the CPU paint fallback without requiring a GPU.
try:
    import numpy as np
    from PyQt6.QtGui import QColor, QImage, QPainter
    from core.avatar import HoloAvatar

    image = QImage(360, 420, QImage.Format.Format_ARGB32_Premultiplied)
    background = QColor(7, 12, 20)
    image.fill(background)
    avatar = HoloAvatar()
    for _ in range(12):
        avatar.step(1 / 30, 0.35, speaking=True, state="SPEAKING")
    painter = QPainter(image)
    avatar.paint(painter, 180, 210, 120, QColor("#38d8ff"), QColor("#bb6cff"), background)
    painter.end()
    visible_pixels = sum(
        image.pixelColor(x, y) != background
        for y in range(image.height()) for x in range(image.width())
    )
    check("avatar pose stays finite", bool(np.isfinite(avatar._pose()[0]).all()))
    check("avatar raster render", visible_pixels > 10_000)
except Exception as exc:
    check(f"avatar render ({exc})", False)

# The audio batch and viseme schedule must stay tied to 100 ms and 20 ms.
try:
    import numpy as np
    import main as jarvis_main

    pcm = (np.sin(np.arange(4_800, dtype=np.float32) * (2 * np.pi * 440 / 24_000)) * 12_000).astype(np.int16)
    frames = jarvis_main._pcm_visemes(pcm)
    check("audio batch is 100 ms mono int16", jarvis_main.MAX_AUDIO_BATCH_BYTES == 4_800)
    check("viseme schedule covers 100 ms", len(frames) == 10 and all(len(frame) == 3 for frame in frames))
    check("viseme FFT bank is cached", 24_000 in jarvis_main._VIS_BANDS_CACHE)
except Exception as exc:
    check(f"audio/viseme pipeline ({exc})", False)

# Packaging and face assets must be present and parseable.
try:
    spec = (ROOT / "jarvis.spec").read_text(encoding="utf-8")
    ast.parse(spec)
    check("PyInstaller spec parses", True)
except Exception as exc:
    check(f"PyInstaller spec ({exc})", False)
check("PyInstaller spec exists", (ROOT / "jarvis.spec").is_file())
check("build script exists", (ROOT / "build-exe.ps1").is_file())
check("avatar model is present", (ROOT / "core" / "face_model.obj").is_file())

print()
if fails:
    print(f"FAILED: {len(fails)} — " + ", ".join(fails))
    sys.exit(1)
print("ALL SMOKE CHECKS PASSED")
