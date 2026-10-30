# PROJECT_CAPABILITIES

Date of last audit: 2026-10-25 (local workspace)

This map reflects the repository as inspected in the current working tree. It separates
confirmed capabilities from assumptions. Status values: works, partial, missing, broken,
unverified, unavailable in current environment.

## Core runtime

- Name: JARVIS live session runtime
- Location: `main.py`
- Status: works (requires Gemini API key, network, microphone/speakers for full voice use)
- Evidence: `tests/test_main.py` imports `main`; `JarvisLive` coordinates session, tools, memory, UI callbacks
- Dependencies: Gemini Live API, PyQt6, local audio devices, action/plugin loaders
- Next improvements: broader automated coverage for tool-learning integration paths

## User interface

- Name: PyQt6 HUD and chat surfaces
- Location: `ui.py`
- Status: works (chat scroll position and bubble geometry have offscreen regression coverage; full visual appearance is not verified)
- Evidence: `tests/test_ui_chat_scroll.py` includes scroll-to-latest blank-space regression and MainWindow integration coverage; full suite -> 207 passed (2026-10-26)
- Next improvements: verify the chat's visual layering and layout on a real display.

## Action system

- Name: Self-describing built-in actions
- Location: `actions/`, `core/action_loader.py`
- Status: works
- Evidence: `tests/test_action_loader.py`; actions expose module-level `TOOL`
- Next improvements: keep new actions small and reversible

## Plugin system

- Name: Dynamic plugins
- Location: `plugins/`, `core/plugin_loader.py`
- Status: works
- Evidence: `tests/test_plugin_loader.py`
- Next improvements: per-plugin settings validation

## Memory

- Name: Long-term memory and behavioural lessons
- Location: `memory/memory_manager.py`, `memory/lessons.py`, `actions/learning.py`
- Status: works (lesson store is local JSON and ignored by Git)
- Evidence: `tests/test_lessons.py` -> 23 passed; lesson integration present in `main.py`
- Next improvements: more tests for correction detection in live voice/text paths

## Telegram remote

- Name: Telegram personal MCP server and HUD cards
- Location: `mcp_servers/telegram_personal/server.py`, `dashboard/server.py`, `ui.py`
- Status: partial/works (media transcription depends on network/model availability)
- Evidence: `tests/test_telegram_personal.py` -> 4 passed
- Next improvements: media size/type edge cases and offline fallback

## GitHub research/integration

- Name: GitHub scout and reviewed integration
- Location: `actions/github_scout.py`, `actions/github_integrate.py`
- Status: works
- Evidence: `tests/test_github_integration.py` -> passed
- Next improvements: keep third-party code isolated in `vendor_scout/`

## Health and incidents

- Name: Diagnostics, health checks, incident registry
- Location: `autonomy/`
- Status: works
- Evidence: `tests/test_main.py` module status report tests
- Next improvements: expose stable test entry points

## Background tasks

- Name: Controlled background execution
- Location: `background.py`
- Status: works (library; requires explicit task registration)
- Evidence: `tests/test_background.py` -> 6 passed
- Limits: concurrency, retries, runtime timeout, pause condition, stop-all
- Next improvements: persist task state and expose an action that runs only whitelisted tasks

## Windows 11 automation

- Name: Windows system helpers
- Location: `windows_automation.py`
- Status: works (best-effort; some operations need administrator rights)
- Evidence: `tests/test_windows_automation.py` -> 8 passed
- Covered: Wi-Fi state/toggle, restart/shutdown/cancel, lock, Settings pages, volume/brightness where supported
- Note: `actions/computer_settings.py` remains the voice-facing action; the helper library does not duplicate it.
- Next improvements: thin integration into `computer_settings.py` to remove duplicated PowerShell snippets

## Testing

- Name: Automated test suite
- Location: `tests/`
- Status: works
- Evidence: `.\\.venv\\Scripts\\python.exe -m pytest -q` -> 207 passed (2026-10-26); CI also runs pytest and `autonomy.health.run_health()`.
- Limitation: visual appearance is not verified by offscreen GUI tests; the Gemini API and network are not required by the automated suite.
- Next improvements: retain time-relative fixtures for tests involving expiring telemetry or persisted history.
