# LIMITATIONS

Last updated: 2026-10-26

## LIM-001: Third-party tests polluted the default test run

- Status: RESOLVED
- Impact: medium
- Evidence: a root-level `pytest` run previously collected `vendor_scout/AegeanWorks-NeuroCore/tests` and reported 93 collection errors.
- Cause: pytest default discovery descended into vendored third-party code.
- Resolution: `pyproject.toml` now sets `testpaths = ["tests"]` and ignores `vendor_scout/`.
- Verification: `pytest` now runs 203 tests successfully.

## LIM-002: Unused copied module manager cannot import

- Status: RESOLVED
- Impact: low
- Evidence: `python -c "import core.module_manager"` previously failed with `ModuleNotFoundError: No module named 'core.flow_runner'`; `core.settings` was also unavailable.
- Resolution: removed the unused duplicate `core/module_manager.py`. The original remains available in `vendor_scout/AegeanWorks-NeuroCore/` if integration is ever needed.
- Verification: full test suite still passes (203 tests).

## LIM-003: Learning integration has limited automated coverage

- Status: RESOLVED
- Impact: medium
- Evidence: `memory/lessons.py` has 23 tests, but `main.py` integration helpers (`_is_tool_failure_result`, `_tool_learning_note`, `_observe_user_turn`) were not directly tested.
- Resolution: `tests/test_main_learning.py` now covers `_is_tool_failure_result`, `_lesson_note`, `_tool_learning_note` and `_observe_user_turn` without instantiating the GUI.
- Verification: `.\.venv\Scripts\python.exe -m pytest tests\test_main_learning.py -q` -> 5 passed.

## LIM-004: Visual verification is not available offscreen

- Status: DETECTED
- Impact: low
- Evidence: `PROJECT_STATE.md` states that visual verification in offscreen mode is impossible and geometry was checked programmatically.
- Next step: keep programmatic assertions in UI tests; do not claim visual correctness without a real display.

## LIM-005: External model and network dependencies

- Status: DETECTED
- Impact: high
- Evidence: README requires a Gemini API key and internet; free-tier quotas and availability are not guaranteed.
- Next step: preserve graceful fallbacks and report unavailable capabilities instead of assuming success.

## LIM-006: Uncommitted feature work in the working tree

- Status: DETECTED
- Impact: medium
- Evidence: `git status --short` shows modified tracked files and untracked learning/lesson files plus `vendor_scout/AegeanWorks-NeuroCore/`.
- Next step: review and commit or revert in small batches; do not overwrite user changes.

## LIM-007: Resource-history regression test depended on a fixed calendar date

- Status: RESOLVED
- Impact: low (test reliability; no runtime behavior was affected)
- Evidence: on 2026-10-26, `.\\.venv\\Scripts\\python.exe -m pytest -q` failed `test_resource_history_stores_aggregates_and_reports_recurring_peak` because its 2026-10-19 samples had aged out of the seven-day history window.
- Cause: the test fixture used a calendar date instead of a timestamp relative to the test run.
- Resolution: construct the sample and pruning fixture from the current local time.
- Verification: full suite -> 203 passed on 2026-10-26.
