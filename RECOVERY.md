# RECOVERY

## Basic recovery

1. Check repository state:

```powershell
git status --short
git diff --stat
```

2. Run the test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

3. If a change breaks tests, revert only the affected files:

```powershell
git checkout -- path/to/file.py
```

## Runtime recovery

- If `memory/lessons.json` or `memory/long_term.json` is corrupted, JARVIS treats an unreadable store as empty rather than crashing.
- If the UI fails to load history, the failure is logged and startup continues.
- Keep `.venv` and user data outside Git-tracked source where possible.

## High-risk operations

- Do not delete untracked user data without confirmation.
- Do not modify registry, services, startup entries or security settings without an explicit plan and rollback.
- For large changes, use a separate branch or worktree and run the full test suite before integration.