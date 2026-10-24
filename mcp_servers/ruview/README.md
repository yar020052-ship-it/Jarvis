# RuView MCP for JARVIS

JARVIS starts the pinned `@ruvnet/ruview@0.9.1` MCP server over stdio from the existing Node.js installation. The RuView tools appear after JARVIS restarts.

The integration exposes local guidance, diagnostics, training/calibration plans and execution tools, and sensing tools. JARVIS asks before tools marked as writes. Cloud workspace and SSH host tools are excluded from the allowlist. No RuView grant or credential is configured by this integration.

Live Wi-Fi sensing needs compatible CSI hardware (for example ESP32-S3/C6). The Docker quick start uses simulated data. RuView does not consume the Android app's camera stream. Hardware access requires an explicit `RUVIEW_MCP_GRANTS=device-access` setup; firmware flashing and workspace writes have additional upstream gates.
