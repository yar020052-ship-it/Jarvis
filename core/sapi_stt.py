"""One-shot offline speech recognition for PCM captured by JARVIS's mic stream.

Uses Windows Speech Recognition through System.Speech, so no second process
opens the microphone and no audio is sent to an ASR service.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import wave


_POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Speech
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$engine.SetInputToWaveFile($env:JARVIS_OFFLINE_WAVE)
$result = $engine.Recognize([TimeSpan]::FromSeconds(12))
if ($result) { [Console]::Out.Write($result.Text) }
$engine.Dispose()
"""


def recognize_pcm(pcm: bytes, sample_rate: int = 16000) -> str:
    """Transcribe signed 16-bit mono PCM with the installed Windows SAPI engine."""
    if sys.platform != "win32":
        raise RuntimeError("Офлайн-распознавание Windows SAPI доступно только в Windows.")
    if not pcm:
        return ""
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm)
        env = dict(os.environ)
        env["JARVIS_OFFLINE_WAVE"] = path
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            detail = (result.stderr or "").strip()[:240]
            raise RuntimeError(detail or "Windows SAPI не смог распознать аудио.")
        return (result.stdout or "").strip()
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
