"""JARVIS TTS — говорит вместо пользователя через динамики ПК.

Поддерживает русский и английский:
- Русский: онлайн-синтез (Google Translate TTS) + воспроизведение MP3 через MCI (winmm).
- Английский/оффлайн: Windows SAPI (PowerShell) — работает без интернета.

Публичный API:
    speak(text, wait=True) -> bool
    synthesize(text) -> Optional[str]   # путь к аудиофайлу
    play_file(path, wait=True) -> bool
"""
from __future__ import annotations

import os
import threading
import subprocess
import tempfile
import base64
import ctypes
import urllib.request
import urllib.parse

_TMP = tempfile.gettempdir()
SAPI_VOICE_HINT = None  # e.g. "Irina" если установлен русский голос


def _has_cyrillic(text: str) -> bool:
    return any("\u0400" <= c <= "\u04FF" for c in text)


# --------------------------------------------------------------------------
# Онлайн-синтез (Google Translate TTS) -> MP3
# --------------------------------------------------------------------------
def _google_tts_chunk(text: str, lang: str, idx: int, total: int, path: str) -> bool:
    q = urllib.parse.quote(text)
    url = (f"https://translate.google.com/translate_tts?ie=UTF-8&q={q}&tl={lang}"
           f"&client=tw-ob&total={total}&idx={idx}&textlen={len(text)}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if not data:
            return False
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def _split_chunks(text: str, limit: int = 190) -> list[str]:
    words = text.split()
    chunks, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > limit:
            if cur:
                chunks.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        chunks.append(cur)
    return chunks or [text[:limit]]


def synthesize_online(text: str, path: str, lang: str = "ru") -> bool:
    """Синтез через Google Translate TTS. Если текст длинный — несколько файлов, играем по очереди."""
    chunks = _split_chunks(text)
    if len(chunks) == 1:
        return _google_tts_chunk(chunks[0], lang, 0, 1, path)
    # несколько частей: сохраняем как path.partN.mp3
    ok = True
    parts = []
    for i, ch in enumerate(chunks):
        p = f"{path}.part{i}.mp3"
        if _google_tts_chunk(ch, lang, i, len(chunks), p):
            parts.append(p)
        else:
            ok = False
    if not parts:
        return False
    # склеиваем бинарно (MP3 конкатенация валидна для воспроизведения)
    with open(path, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                out.write(f.read())
    for p in parts:
        try:
            os.remove(p)
        except Exception:
            pass
    return ok


# --------------------------------------------------------------------------
# SAPI оффлайн-синтез -> WAV
# --------------------------------------------------------------------------
def _run_ps(script: str, timeout: float = 60.0):
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                          capture_output=True, timeout=timeout,
                          text=True, encoding="utf-8", errors="replace")


def synthesize_sapi(text: str, path: str, rate: int = -1) -> bool:
    esc = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {rate}; "
        f"$s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak('{esc}'); "
        "$s.Dispose()"
    )
    try:
        _run_ps(script)
    except Exception:
        return False
    return os.path.isfile(path) and os.path.getsize(path) > 1000


def sapi_speak_direct(text: str) -> bool:
    """Прямая (не в файл) озвучка через SAPI. Годится для латиницы."""
    esc = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = -1; $s.Speak('{esc}'); $s.Dispose()"
    )
    try:
        _run_ps(script)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Воспроизведение (MCI/winmm) — без зависимостей, mp3 и wav
# --------------------------------------------------------------------------
def play_file(path: str, wait: bool = True) -> bool:
    if not path or not os.path.isfile(path):
        return False

    def _do():
        mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(512)
        alias = "jarvis_tts"
        try:
            mci(f'close {alias}', buf, 511, 0)
            if mci(f'open "{path}" type mpegvideo alias {alias}', buf, 511, 0) != 0:
                mci(f'close {alias}', buf, 511, 0)
                if mci(f'open "{path}" alias {alias}', buf, 511, 0) != 0:
                    return
            if wait:
                mci(f'play {alias} wait', buf, 511, 0)
            else:
                mci(f'play {alias}', buf, 511, 0)
        finally:
            if wait:
                mci(f'close {alias}', buf, 511, 0)

    if wait:
        _do()
        return True
    threading.Thread(target=_do, daemon=True).start()
    return True


# --------------------------------------------------------------------------
# Высокоуровневый API
# --------------------------------------------------------------------------
def synthesize(text: str, out_path: str | None = None) -> str | None:
    """Возвращает путь к аудиофайлу (mp3/wav) с озвученным текстом или None."""
    if not text:
        return None
    out_path = out_path or os.path.join(_TMP, "jarvis_tts_out.mp3")
    engine = os.getenv("JARVIS_TTS_ENGINE", "auto").lower()

    if engine in ("online", "auto") and _has_cyrillic(text):
        if synthesize_online(text, out_path, lang="ru"):
            return out_path
    # Латынь или онлайн не удался -> SAPI оффлайн
    if engine != "online":
        wav = out_path.rsplit(".", 1)[0] + ".wav"
        if synthesize_sapi(text, wav):
            return wav
    # Последняя попытка — онлайн
    if engine != "sapi" and synthesize_online(text, out_path, lang="ru" if _has_cyrillic(text) else "en"):
        return out_path
    return None


def speak(text: str, wait: bool = True) -> bool:
    """Озвучивает текст (рус/англ)."""
    if not text:
        return False

    # Быстрый оффлайн-путь для латиницы через SAPI (без интернета)
    if not _has_cyrillic(text) and os.getenv("JARVIS_TTS_ENGINE", "auto").lower() != "online":
        if sapi_speak_direct(text):
            return True

    path = synthesize(text)
    if path and play_file(path, wait=wait):
        if wait:
            try:
                os.remove(path)
            except Exception:
                pass
        return True

    print(f"[TTS FAIL] {text}")
    return False


def say(text: str) -> bool:
    return speak(text, wait=True)


def status() -> dict:
    voices = []
    try:
        r = _run_ps(
            "Add-Type -AssemblyName System.Speech; "
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
            "ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture.Name }"
        )
        voices = [v for v in (r.stdout or "").splitlines() if v.strip()]
    except Exception:
        pass
    has_ru = any("ru" in v.lower() for v in voices)
    return {
        "sapi_voices": voices,
        "russian_voice_installed": has_ru,
        "online_tts": True,
        "engine": os.getenv("JARVIS_TTS_ENGINE", "auto"),
    }


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="JARVIS TTS")
    ap.add_argument("text", nargs="*", help="текст для озвучки")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        t = " ".join(a.text) or "JARVIS готов."
        print("speak:", speak(t))
