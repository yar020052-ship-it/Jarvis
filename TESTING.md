# TESTING

## Quick run

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expected current result: `211 passed`.

Last verified: 2026-10-29 with `.\\.venv\\Scripts\\python.exe -m pytest -q` -> `211 passed`.

## Notes

- `pyproject.toml` restricts pytest to `tests/`, so vendored code in `vendor_scout/` is not collected.
- GUI tests use `QT_QPA_PLATFORM=offscreen` where needed.
- Run a focused subset:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_lessons.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_ui_window.py -q
```

## Known test limitations

- Visual appearance is not verified; geometry and widget state are checked programmatically.
- Tests do not require a live Gemini API key or network access.
