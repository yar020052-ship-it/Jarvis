---
name: windows-webcam
description: Diagnose Windows webcam detection, driver state, camera access, and JARVIS camera errors.
---

# Windows webcam diagnostics

Separate device enumeration, driver health, privacy permissions, and camera
application access. Inspect the exact Device Manager status and hardware IDs
before changing anything. Prefer the Windows USB Video Class (UVC) driver when
the camera supports it; do not install driver packs from third-party updater
utilities.

Check whether another application is holding the camera, whether Windows
camera privacy access is enabled for desktop apps, and whether the actual
capture API can open the device. Test candidate camera indices rather than
assuming index 0. Keep any persistent local preview open only while requested,
and do not transmit frames unless the user explicitly asks for visual analysis.

Before removing or replacing a driver, identify a recovery path and capture the
current device status. Verify the camera after every change.
