# SECURITY

## Principles

- API keys, tokens, passwords and private data are kept outside source code.
- Sensitive configuration files are ignored by Git (see `.gitignore`).
- Irreversible or disruptive operations require confirmation through the UI.
- Plugins and actions are discovered and validated before activation.
- Remote Telegram control uses allowlists, pairing, rate limiting and private-chat restrictions.
- Long-term memory and lessons are stored locally.
- Third-party code is isolated under `vendor_scout/` and is not executed by default.

## Secret handling

- Never commit `config/api_keys.json`, `config/mcp_servers.json`, `memory/long_term.json`, `memory/lessons.json` or credential files.
- Do not print secret values in logs or diagnostics.
- Prefer environment variables or local secret files excluded from Git.

## Confirmation model

- Dangerous operations such as shutdown, restart, Wi-Fi changes and similar actions must use the UI confirmation token flow.
- The model must not be able to bypass confirmation by passing `confirmed=yes`.

## Recovery and rollback

- Prefer small, reversible changes.
- Use Git status/diff before risky edits.
- Keep tests passing before and after changes.
- Do not delete user data or change system settings without explicit confirmation.