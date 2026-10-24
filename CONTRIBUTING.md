# Contributing to JARVIS

## Development setup

1. Create a virtual environment and install dependencies:
   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\pip install pytest pre-commit ruff black mypy pyright
   ```
2. Install pre-commit hooks:
   ```powershell
   .venv\Scripts\pre-commit install
   ```

## Running tests

```powershell
.venv\Scripts\python -m pytest -q
```

Health check (syntax + import smoke):

```powershell
.venv\Scripts\python -c "from autonomy import health; print(health.run_health().text())"
```

## Code style

- Format with `black` and lint with `ruff`.
- Type-check `core/*` with `mypy`/`pyright` (gradual typing; new code should be typed).
- Keep changes focused and covered by tests when possible.

## Adding an action or plugin

- Create a file in `actions/` or `plugins/` exposing a `TOOL`/`PLUGIN` dict and a handler.
- Follow the contract in `core/action_loader.py` / `core/plugin_loader.py`.
- Add a test under `tests/` when the module has non-trivial logic.

## Pull requests

- Run `pre-commit run --all-files` and `pytest` before opening a PR.
- Describe the change and how it was tested.
- Do not commit secrets, local configs, logs, caches or backups (see `.gitignore`).