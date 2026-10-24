---
name: android-troubleshooting
description: Diagnose Android connection, ADB over USB or Wi-Fi, app control, and common device permission problems.
---

# Android troubleshooting

Use the existing `android_control` action for every device operation. First
check device state and report which transport is active. Do not guess that a
device is connected just because it was connected in an earlier session.

For Wi-Fi debugging, explain and verify the supported Android Wireless
debugging pairing flow: computer and device on the same trusted local network,
Wireless debugging enabled by the user in Developer options, pair using the
device's pairing code and pairing port, then connect using the separate ADB
connection port. Never expose pairing codes or device addresses in a public
message. Recheck device state after each connection attempt.

For USB issues, inspect the exact ADB state and Windows device status before
changing drivers. Prefer the platform-tools ADB interface and Windows' built-in
drivers; do not recommend driver-updater utilities. Never unlock the bootloader,
disable Android security, or run arbitrary shell commands to bypass permissions.

For app or settings tasks, inspect the screen, interact with visible controls,
and verify each change. Stop and explain if Android requires an on-device
permission, unlock, or confirmation.
