# Changelog

All notable changes to JARVIS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.1] - 2026-10-26

### Added
- `GITHUB_CANDIDATES.md`: registry of GitHub candidates considered for code/idea adoption, including the `AegeanWorks/NeuroCore` evaluation (AGPL-3.0, status: reference only).

### Changed
- `background.py`: tasks now track last status/error, run count, start/finish timestamps, and manager persists recent task history under `.jarvis_background/tasks_history.json`.

### Fixed
- No fixes in this cycle; full test suite re-run after documentation and background-state changes.

## [0.2.0] - 2026-10-25

### Added
- **Background task system** (`background.py`): Controlled background execution with resource limits, retry logic, pause/check conditions, status tracking, and JSON history persisted under `.jarvis_background/` for recovery after restart.
- **Background tasks action** (`actions/background_tasks.py`): Voice-facing `background_tasks` tool with a whitelist of safe tasks (`ping`, `lesson_stats`, `model_status`).
- **Windows 11 automation** (`windows_automation.py`): Safe Windows operations (Wi-Fi toggle, workstation lock, settings opening) with UI confirmation requirements.
- **Sticky-scroll in ChatWidget** (`ui.py`): Autoprocroll only when user is at bottom; "↓" button appears otherwise to return to latest message. Public aliases: `is_at_bottom()`, `scroll_to_bottom()`, `fit_bubbles()`, `bubble_max_width()`.
- **Behavioural lessons integration** (`main.py`): `_is_tool_failure_result`, `_lesson_note`, `_tool_learning_note`, `_observe_user_turn` — detects and stores tool failures and owner corrections for the learning system.
- **New test files**:
  - `tests/test_main_learning.py` — 5 tests for failure detection and lesson notes.
  - `tests/test_ui_chat_scroll.py` — 3 tests for sticky-scroll behavior.
  - `tests/test_lesson_integration.py` — 5 tests for lessons integration.
  - `tests/test_lesson_edge_cases.py` — 7 tests for edge cases in the lesson store.
  - `tests/test_background.py` — 6 tests for the background task system.
  - `tests/test_windows_automation.py` — 8 tests for Windows helpers.
- **Security documentation** (`SECURITY.md`): Principles, secret handling, confirmation model, recovery procedures.
- **Recovery documentation** (`RECOVERY.md`): Basic recovery steps, runtime recovery, high-risk operation guidelines.
- **Testing documentation** (`TESTING.md`): Quick test commands, known limitations.
- **Capabilities map** (`PROJECT_CAPABILITIES.md`): Map of project capabilities with statuses and evidence.
- **Limitations registry** (`LIMITATIONS.md`): 6 confirmed limitations with IDs, impact levels, and resolutions.
- **Project state** (`PROJECT_STATE.md`): Updated architecture, verified changes, commands run, and next tasks.

### Changed
- **`pyproject.toml`**: Added `[tool.pytest.ini_options]` with `testpaths` and `norecursedirs` to exclude `vendor_scout/`.
- **`core/module_manager.py`**: Removed unused duplicate (was blocking imports).
- **`ui.py`**: Rewrote scroll logic with sticky behavior and scroll button; added `_position_scroll_button`, `_is_at_bottom`, `_update_scroll_button`, `_scroll_after_message`.
- **`main.py`**: Integrated lesson note generation into tool result pipeline; added failure-shape recognition (`_is_tool_failure_result`).
- **`.gitignore`**: Added `memory/lessons.json` to git tracking exclusions.

### Fixed
- `pytest` from root no longer collects third-party tests from `vendor_scout/AegeanWorks-NeuroCore/`: 93 collection errors eliminated.
- `core/module_manager.py` import error resolved by removing the unused duplicate.
- ChatWidget bubbles now re-flow on show (`showEvent`) and on resize (`resizeEvent`).
- Tool failure results in `main.py` correctly recognized and fed into the lesson system.

### Deprecated
- None.

### Removed
- Unused `core/module_manager.py` (was a duplicate from `vendor_scout/` and could not import).

## [0.1.0] - 2026-10-08

### Added
- Initial project structure and audit.
- Core JARVIS functionality.
- Existing test suite (164 tests at the time).
</