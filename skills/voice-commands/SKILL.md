---
name: voice-commands
description: Use when the owner asks about their saved commands (the Command Centre), wants to run, inspect or extend them, asks what phrases JARVIS knows, or when a request sounds like one of their routine phrases or scenes.
---

# Command Centre — the owner's saved commands

The owner keeps a personal library of saved phrases — each one a short script
of exact steps in their own wording. It lives in `.jarvis/commands/library.json`
in this project (about 100 commands in 13 packs: windows, browser, media,
youtube, telegram, music, vscode, discord, steam, office, ai, reading,
scenarios).

## Tools

- `voice_command(phrase)` — run one saved phrase, e.g. «режим кино»,
  «доброе утро», «открой ютуб», «сделай тише», «диктовка». Pass the request as
  close to the owner's words as possible. The result either reports what ran or
  lists the closest saved commands when nothing matched.
- `voice_command_library(action, value)` — inspect and extend:
  - `list` — every saved command with its phrases;
  - `search` + value — find by keyword (use it when unsure whether a request
    names a saved phrase);
  - `packs` — ready-made packs; `install_pack` + value — add one;
  - `history` — what ran lately; `status` — sizes and paths.

## Rules

- Prefer `voice_command` over hand-building the same thing when the request
  looks like a routine phrase or a scene. A saved command is the owner's own
  wording and their own sequence — it must behave the same every time.
- If the library has nothing, answer the request normally with your own tools.
- Dangerous saved commands (shutdown, sleep, lock…) raise the confirmation
  banner on the HUD themselves — never claim one is done before the owner has
  confirmed it.
- Typed phrases that exactly match a saved command run locally, without you. If
  a result says «только что выполнена локально», just confirm it in one
  sentence — do not run it again.
- The library file is the source of truth; do not edit the JSON by hand. To add
  a pack call `voice_command_library` with action=install_pack.
