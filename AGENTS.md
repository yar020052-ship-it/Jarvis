# JARVIS — dev commands

## Environment
Python `.venv` at repo root. Activate:
```
.\.venv\Scripts\Activate.ps1
```

## Lint / typecheck (installed into `.venv`)
Ruff, mypy, black are installed. Run from repo root:

- `.\.venv\Scripts\ruff.exe check .` — lint (config: `[tool.ruff]` in pyproject.toml; select E/F/I/UP/B/SIM, ignore E501)
- `.\.venv\Scripts\mypy.exe core` — typecheck (config: `[tool.mypy]` in pyproject.toml; checks `core/`)
- `.\.venv\Scripts\black.exe --check .` — formatting check

Notes:
- The codebase has **pre-existing** ruff/mypy/black violations (vendor_scout and elsewhere), so these commands report existing findings. My edits add no new ruff violations in the touched regions (`main.py`, `core/mcp_registry.py`).
- mypy reports mostly missing library stubs (ctranslate2, faster_whisper, vosk, funasr, google.genai, httpx2, core.paths, memory.config_manager, core.secrets) — environmental, not code defects.

## Tests
```
.\.venv\Scripts\pytest.exe
```
Config: `[tool.pytest.ini_options]` in pyproject.toml (`testpaths = ["tests"]`, excludes `.venv`, `vendor_scout`, `build`, `dist`).

## Notes
- No AGENTS.md existed before; commands added here for reference.
- Do NOT run `black --write` or `ruff --fix --unsafe-fixes` across the whole repo — that would reformat large amounts of unrelated pre-existing code.
