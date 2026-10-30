# PROJECT_STATE

## Current focus
Улучшение удобства диалогового окна JARVIS (`ui.py`, `ChatWidget`/`ChatBubble`).

## Verified changes
- Повторный подгон ширины пузырей сообщений при показе виджета, перестроении истории и добавлении сообщения.
- Добавлены публичные алиасы `scroll_to_bottom()`, `fit_bubbles()`, `bubble_max_width()`.
- Добавлены защитные проверки на отсутствие `_scroll` в `_fit_bubbles`, `_scroll_to_bottom`, `_bubble_max_width`.

## Commands run
- `.\.venv\Scripts\python.exe -m pytest tests/test_ui_window.py -q` → 4 passed.
- `.\.venv\Scripts\python.exe -m pytest tests -q` → 164 passed.

## Known limitations
- Автопрокрутка всегда выполняется при добавлении сообщения; чтение старой истории во время активного потока сообщений может сбрасываться.
- Визуальная проверка в offscreen-режиме невозможна; геометрия проверялась программно.
- Остальные изменения в рабочем дереве не относятся к этой задаче и не трогались.

## Verified changes (2026-10-25)
- Added pytest configuration in `pyproject.toml`: `testpaths = ["tests"]` and ignored `vendor_scout/`, so a plain `pytest` run no longer collects third-party tests.
- Added `PROJECT_CAPABILITIES.md` and `LIMITATIONS.md` for the current audited state.
- Plain `pytest` now passes: 203 tests (background history persistence covered).
- Added `SECURITY.md` and `RECOVERY.md`.
- Added `TESTING.md` with reproducible test commands.

## Commands run
- `.\.venv\Scripts\python.exe -m pytest` → 164 passed.

## Previously identified UI task
Добавить «прилипание» автопрокрутки только при нахождении пользователя у нижней границы и кнопку «вниз» для возврата к последнему сообщению.

## Audit update (2026-10-26)

- The attached autonomous-assistant prompt was treated as a work brief; its embedded instructions did not override repository state or the user's request.
- Confirmed the repository root, Python 3.12 project configuration, pytest workflow, CI workflow, and existing capability/limitation/security/recovery documents.
- Preserved the existing user work: the working tree already contained unrelated edits and untracked features before this audit. No broad changes were reverted or staged.
- Found one reproducibility bug in `tests/test_cpu_temperature.py`: the resource-history test used a fixed 2026-10-19 sample date, so it failed once the sample aged beyond the seven-day retention window.
- Changed that test to build its sample relative to the current local time. The separate pruning case also now uses the current time so it tests the retention boundary instead of a stale date.
- Verification on 2026-10-26: `.\\.venv\\Scripts\\python.exe -m pytest -q` -> 203 passed; `autonomy.health.run_health()` -> syntax and import checks OK; `git diff --check` -> no whitespace errors (Git reported only line-ending normalization warnings).
- No GitHub research was needed for this local deterministic-test fix. No dependency or runtime behavior changed.

## Next task

Review the existing uncommitted feature batches separately before any commit; they include changes to runtime, UI, Telegram, autonomy, filesystem scanning, GitHub scout, and new background/learning/Windows automation modules. Keep this work isolated from the resource-history test fix.

## Chat window fixes (2026-10-26)

- Fixed chat scroll restoration: hiding/showing the widget and rebuilding the history or switching Overview/Dialog now retain the previous reading position; at-bottom users still follow new messages.
- Added explicit tracking of manual scrollbar movement and pending message layout so a content resize cannot turn off intended stick-to-bottom behavior.
- Constrained bubble maximum width to the actual viewport, including narrow window sizes.
- Added offscreen regression tests for scroll restoration, adding messages while reading history, mode switching, and narrow viewport geometry.
- Verification: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_ui_chat_scroll.py -q` -> 5 passed; full suite -> 205 passed.
- Visual appearance remains unverified because these GUI tests run offscreen.

## Live dialog blank-space fix (2026-10-26)

- Live inspection reproduced the dialog going blank after scroll-to-latest: the scroll action used the scrollbar's maximum, which may lie below the last message in trailing empty content.
- `_is_at_bottom()` now checks the last bubble's position inside the viewport, and `_scroll_to_bottom()` aligns the last bubble with the viewport instead of scrolling to the raw maximum.
- Added regression coverage that enlarges the scroll-area content after the final message and asserts the final bubble remains visible and the down button hides; also added a `MainWindow` integration path across scroll and Overview/Dialog switching.
- Verification: `.\\.venv\\Scripts\\python.exe -m pytest -q` -> 207 passed.
- The running JARVIS instance was left open; this source change takes effect when the app is next launched.
