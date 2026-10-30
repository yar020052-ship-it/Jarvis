"""JARVIS Voice — слух и голос (как голосовое управление Luxify, но своё).

Что умеет:
  * слушает микрофон ПК и распознаёт речь: офлайн (Vosk, Windows SAPI)
    и онлайн (Google, OpenAI Whisper);
  * понимает команды командного центра (jarvis_commands) — одна фраза,
    цепочка действий; если это не команда — спрашивает ИИ и отвечает
    голосом (как Luxify AI);
  * wake word «Джарвис»: демон слушает фон и срабатывает на имя;
  * голоса-профили (Джарвис, Пятница, GLaDOS, Мита, Альтрон): с edge-tts
    можно менять тембр/темп, без него — базовый синтез;
  * подтверждение опасных команд голосом («Скажите да или нет»).

CLI:
    python jarvis_voice.py status            # что доступно (микрофон, движки)
    python jarvis_voice.py once --sec 5      # послушать 5 сек -> выполнить
    python jarvis_voice.py listen --sec 5    # только распознать (без команд)
    python jarvis_voice.py say "Добрый день"
    python jarvis_voice.py daemon            # слушать в фоне с wake word
    python jarvis_voice.py stop              # остановить демон
    python jarvis_voice.py profiles          # голоса; --set friday
    python jarvis_voice.py selftest

Настройки (переменные окружения):
    JARVIS_ASR=auto|vosk|google|whisper   # порядок распознавания
    JARVIS_VOSK_MODEL=<путь к модели vosk>
    JARVIS_VOICE_PROFILE=jarvis           # голос по умолчанию
    JARVIS_VOICE_LANG=ru-RU
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

VOICE_DIR = os.path.join(ROOT, ".jarvis", "voice")
CACHE_DIR = os.path.join(VOICE_DIR, "cache")
PROFILES_PATH = os.path.join(VOICE_DIR, "voices.json")
PID_PATH = os.path.join(VOICE_DIR, "daemon.pid")
STOP_PATH = os.path.join(VOICE_DIR, "stop")
LOG_PATH = os.path.join(VOICE_DIR, "daemon.log")
LAST_PATH = os.path.join(VOICE_DIR, "last.json")

SAMPLE_RATE = 16000
MIN_RMS = 60.0                      # тише — считаем тишиной, не гоняем в сеть
WAKE_WORDS = ("джарвис", "джарвиз", "джервис", "жарвис", "jarvis")
STOP_WORDS = ("стоп", "замолчи", "хватит слушать", "остановись")
YES_WORDS = ("да", "верно", "подтверждаю", "ага", "конечно", "yes", "окей",
             "ок")

# публичный ключ chromium, которым пользуется свободное распознавание Google
_GOOGLE_KEY = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"

# --------------------------------------------------------------------------- #
#  Голоса-профили (характеры)
# --------------------------------------------------------------------------- #

BUILTIN_PROFILES = {
    "jarvis": {
        "name": "Джарвис", "voice": "", "rate": -1, "pitch": 0,
        "greet": "Слушаю, сэр.", "ok": "Готово, сэр.",
        "error": "Не вышло, сэр.", "ask": "Секунду, сэр.",
        "edge_voice": "ru-RU-DmitryNeural",
    },
    "friday": {
        "name": "Пятница", "voice": "", "rate": 0, "pitch": 25,
        "greet": "Слушаю.", "ok": "Готово.",
        "error": "Не получилось.", "ask": "Одну секунду.",
        "edge_voice": "ru-RU-SvetlanaNeural",
    },
    "glados": {
        "name": "GLaDOS", "voice": "", "rate": -3, "pitch": -15,
        "greet": "Я слушаю. Как всегда.", "ok": "Выполнено. Конечно же.",
        "error": "Не вышло. Удивительно, правда?", "ask": "Обрабатываю.",
        "edge_voice": "ru-RU-SvetlanaNeural",
    },
    "mita": {
        "name": "Мита", "voice": "", "rate": 2, "pitch": 35,
        "greet": "Я тут!", "ok": "Готово!", "error": "Ой, не вышло.",
        "ask": "Сейчас-сейчас!",
        "edge_voice": "ru-RU-SvetlanaNeural",
    },
    "ultron": {
        "name": "Альтрон", "voice": "", "rate": -3, "pitch": -30,
        "greet": "Слушаю.", "ok": "Сделано.",
        "error": "Неудача.", "ask": "Считаю.",
        "edge_voice": "ru-RU-DmitryNeural",
    },
}


def load_profiles() -> dict:
    """Профили: встроенные + пользовательские из voices.json."""
    out = {k: dict(v) for k, v in BUILTIN_PROFILES.items()}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for k, v in (data.get("profiles") or {}).items():
            out.setdefault(k, {}).update(v)
        out["_default"] = data.get("default") or os.environ.get(
            "JARVIS_VOICE_PROFILE", "jarvis")
    except (OSError, json.JSONDecodeError):
        out["_default"] = os.environ.get("JARVIS_VOICE_PROFILE", "jarvis")
    if out["_default"] not in out:
        out["_default"] = "jarvis"
    return out


def get_profile(profile: Optional[str] = None) -> tuple[str, dict]:
    profs = load_profiles()
    pid = profile or profs.get("_default") or "jarvis"
    if pid not in profs or pid.startswith("_"):
        pid = "jarvis"
    return pid, {k: v for k, v in profs[pid].items()}


def set_default_profile(pid: str) -> dict:
    profs = load_profiles()
    if pid not in profs or pid.startswith("_"):
        raise ValueError(f"нет профиля «{pid}». Есть: "
                         + ", ".join(k for k in profs if not k.startswith("_")))
    os.makedirs(VOICE_DIR, exist_ok=True)
    data = {"default": pid, "profiles": {}}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            data.update(json.load(f) or {})
    except (OSError, json.JSONDecodeError):
        pass
    data["default"] = pid
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return {"ok": True, "default": pid, "name": profs[pid].get("name", pid)}


# --------------------------------------------------------------------------- #
#  Микрофон (sounddevice) и тишина
# --------------------------------------------------------------------------- #

class VoiceError(RuntimeError):
    pass


def _sd():
    try:
        import sounddevice  # noqa: F401
        return sounddevice
    except Exception as e:  # noqa: BLE001
        raise VoiceError(
            f"нет sounddevice (pip install sounddevice): {e}") from e


def record(seconds: float = 4.0, sr: int = SAMPLE_RATE) -> bytes:
    """Записать с микрофона ПК PCM16 mono. Возвращает bytes."""
    sd = _sd()
    seconds = max(0.5, min(60.0, float(seconds)))
    try:
        arr = sd.rec(int(seconds * sr), samplerate=sr, channels=1,
                     dtype="int16")
        sd.wait()
    except Exception as e:  # noqa: BLE001
        raise VoiceError(f"микрофон недоступен: {e}") from e
    return arr.tobytes()


def rms(pcm: bytes) -> float:
    try:
        import numpy as np
        x = np.frombuffer(pcm, dtype=np.int16).astype("float64")
        if x.size == 0:
            return 0.0
        return float((x * x).mean() ** 0.5)
    except Exception:
        if not pcm:
            return 0.0
        vals = struct.unpack(f"<{len(pcm) // 2}h", pcm[:len(pcm) // 2 * 2])
        if not vals:
            return 0.0
        return (sum(v * v for v in vals) / len(vals)) ** 0.5


def wav_bytes(pcm: bytes, sr: int = SAMPLE_RATE) -> bytes:
    n = len(pcm)
    return (struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + n, b"WAVE",
                        b"fmt ", 16, 1, 1, sr, sr * 2, 2, 16, b"data", n)
            + pcm)


def mic_info() -> dict:
    try:
        sd = _sd()
        devs = sd.query_devices()
        inp = sd.query_devices(kind="input")
        return {"available": True, "default": str(inp.get("name", "")),
                "count": len(devs)}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:200]}


# --------------------------------------------------------------------------- #
#  Распознавание речи: Vosk (офлайн) / Windows SAPI (офлайн) / Google / Whisper
# --------------------------------------------------------------------------- #

def _vosk_model_dir() -> Optional[str]:
    env = os.environ.get("JARVIS_VOSK_MODEL", "").strip()
    if env and os.path.isdir(env):
        return env
    base = os.path.join(ROOT, "models")
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isdir(p) and (
                    os.path.isdir(os.path.join(p, "am"))
                    or os.path.isdir(os.path.join(p, "conf"))):
                return p
    return None


def vosk_available() -> tuple[bool, str]:
    try:
        import vosk  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, f"нет пакета vosk ({e})"
    model = _vosk_model_dir()
    if not model:
        return False, ("пакет есть, модели нет — положи модель в models/ "
                       "(например vosk-model-small-ru-0.22)")
    return True, model


_VOSK = {"model": None, "dir": None}


def _vosk_recognize(pcm: bytes, sr: int = SAMPLE_RATE) -> str:
    import json as _json
    import vosk
    model_dir = _vosk_model_dir()
    if _VOSK["model"] is None or _VOSK["dir"] != model_dir:
        vosk.SetLogLevel(-1)
        _VOSK["model"] = vosk.Model(model_dir)
        _VOSK["dir"] = model_dir
    rec = vosk.KaldiRecognizer(_VOSK["model"], sr)
    rec.AcceptWaveform(pcm)
    res = _json.loads(rec.FinalResult())
    return (res.get("text") or "").strip()


def _google_recognize(pcm: bytes, sr: int = SAMPLE_RATE,
                      lang: str = "ru-RU") -> str:
    url = ("https://www.google.com/speech-api/v2/recognize"
           f"?client=chromium&lang={lang}&key={_GOOGLE_KEY}")
    req = urllib.request.Request(
        url, data=pcm,
        headers={"Content-Type": f"audio/l16; rate={sr}"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — сеть/429 -> просто не распознали
            return ""
        for line in body.splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            for res in obj.get("result", []):
                alts = res.get("alternative") or res.get("alternatives") or []
                for alt in alts:
                    t = (alt.get("transcript") or "").strip()
                    if t:
                        return t
        # пустой ответ бывает при мягком лимите — одна повторная попытка
        if attempt == 0:
            time.sleep(0.7)
    return ""


def _whisper_recognize(pcm: bytes, sr: int = SAMPLE_RATE,
                       lang: str = "ru") -> str:
    key = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if not key:
        return ""
    boundary = "----JarvisForm" + secrets.token_hex(8)
    wav = wav_bytes(pcm, sr)
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))

    field("model", "whisper-1")
    if lang:
        field("language", lang)
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="file"; filename="voice.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode("ascii"))
    parts.append(wav)
    parts.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
    body = b"".join(parts)
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return (data.get("text") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def sapi_recognizers() -> list[str]:
    """Установленные распознаватели Windows SAPI (офлайн-слух)."""
    script = ("Add-Type -AssemblyName System.Speech; "
              "[System.Speech.Recognition.SpeechRecognitionEngine]"
              "::InstalledRecognizers() | "
              "ForEach-Object { $_.Culture.Name }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=25, text=True,
                           encoding="utf-8", errors="replace")
        return [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        return []


def _sapi_recognize_live(seconds: float = 5.0) -> str:
    """Распознать одним заходом через Windows SAPI (офлайн, нужен языковой
    пакет: Параметры -> Язык и регион -> Речь)."""
    sec = max(1, min(20, int(seconds)))
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine; "
        "$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar)); "
        "$r.SetInputToDefaultAudioDevice(); "
        f"$res = $r.Recognize([TimeSpan]::FromSeconds({sec})); "
        "if ($res) { [Console]::Out.Write($res.Text) } else { [Console]::Out.Write('') }"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=sec + 20, text=True,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def asr_mode() -> str:
    return (os.environ.get("JARVIS_ASR", "auto") or "auto").strip().lower()


def recognize(pcm: bytes, sr: int = SAMPLE_RATE,
              lang: Optional[str] = None) -> tuple[str, str]:
    """Распознать записанный PCM. Возвращает (текст, движок)."""
    lang = lang or os.environ.get("JARVIS_VOICE_LANG", "ru-RU")
    mode = asr_mode()
    whisper_lang = "ru" if lang.lower().startswith("ru") else "en"

    order = [mode] if mode != "auto" else ["vosk", "google", "whisper"]
    for backend in order:
        try:
            if backend == "vosk":
                ok, _ = vosk_available()
                if not ok:
                    continue
                text = _vosk_recognize(pcm, sr)
            elif backend == "google":
                text = _google_recognize(pcm, sr, lang)
            elif backend == "whisper":
                text = _whisper_recognize(pcm, sr, whisper_lang)
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if mode != "auto" or text:
            return text, backend
        if backend == "vosk" and mode == "auto":
            # vosk не услышал — попробуем следующий движок
            continue
    return "", "none"


# --------------------------------------------------------------------------- #
#  Синтез (голос): edge-tts -> Google -> SAPI, с кэшем фраз
# --------------------------------------------------------------------------- #

def edge_available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _cache_path(text: str, profile: dict, pid: str) -> str:
    key = hashlib.sha1(
        f"{pid}|{text}|{profile.get('rate')}|{profile.get('pitch')}"
        f"|{profile.get('edge_voice')}".encode("utf-8")).hexdigest()[:20]
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{key}.mp3")


def _edge_synth(text: str, path: str, voice: str,
                rate: int, pitch: int) -> bool:
    try:
        import asyncio
        import edge_tts

        async def go() -> None:
            comm = edge_tts.Communicate(
                text, voice or "ru-RU-DmitryNeural",
                rate=f"{int(rate):+d}%", pitch=f"{int(pitch):+d}Hz")
            await comm.save(path)

        asyncio.run(go())
        return os.path.isfile(path) and os.path.getsize(path) > 500
    except Exception:  # noqa: BLE001
        return False


def synthesize(text: str, profile: Optional[dict] = None,
               pid: str = "") -> Optional[str]:
    """Озвучить текст и вернуть путь к файлу (с кэшем повторных фраз)."""
    text = str(text or "").strip()
    if not text:
        return None
    pid, prof = (pid, profile) if profile else get_profile()
    path = _cache_path(text, prof, pid)
    if os.path.isfile(path) and os.path.getsize(path) > 500:
        return path

    # 1) edge-tts — настоящие голоса персонажей (если установлен)
    if edge_available():
        if _edge_synth(text, path, str(prof.get("edge_voice") or ""),
                       int(prof.get("rate", 0) or 0),
                       int(prof.get("pitch", 0) or 0)):
            return path
    # 2) базовый путь проекта: Google (русский) / SAPI (офлайн)
    try:
        import jarvis_tts
        got = jarvis_tts.synthesize(text, out_path=path)
        if got:
            return got
    except Exception:  # noqa: BLE001
        pass
    return None


def say(text: str, wait: bool = True,
        profile: Optional[str] = None) -> bool:
    """Озвучить текст голосом выбранного профиля."""
    pid, prof = get_profile(profile)
    path = synthesize(text, prof, pid)
    if not path:
        return False
    try:
        import jarvis_tts
        return jarvis_tts.play_file(path, wait=wait)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
#  Wake word и обработка фразы
# --------------------------------------------------------------------------- #

def find_wake(text: str) -> tuple[bool, str]:
    """Есть ли в тексте «Джарвис», и что после него. (wake, rest)."""
    s = str(text or "")
    low = s.lower().replace("ё", "е")
    for w in WAKE_WORDS:
        pos = low.find(w)
        if pos >= 0:
            rest = s[pos + len(w):].strip(" ,.!?-—")
            return True, rest
    return False, s.strip()


def strip_wake(text: str) -> str:
    return find_wake(text)[1]


_SENT_SPLIT = None  # зарезервировано


def handle_text(text: str, speak_reply: bool = True,
                profile: Optional[str] = None,
                ok_reply: bool = True) -> dict:
    """Обработать распознанную фразу: команда -> ИИ -> ответ голосом."""
    import jarvis_commands as jc

    out = {"heard": text, "source": "none", "matched": [], "reply": "",
           "ok": False, "error": ""}
    pid, prof = get_profile(profile)
    text = strip_wake(text)
    norm = jc.normalize(text)
    if not norm:
        out["error"] = "пустая фраза"
        return out
    if any(w in norm for w in STOP_WORDS):
        out["source"] = "stop"
        out["ok"] = True
        if speak_reply:
            say("Хорошо.", profile=pid)
        return out

    def _confirm(question: str) -> bool:
        return True

    ctx = jc.ExecContext(speak=False, confirm_fn=_confirm,
                         voice_profile=pid)
    res = jc.run_phrase(text, ctx)
    if res.matched:
        out["matched"] = res.matched
        out["ok"] = res.ok
        out["reply"] = res.reply
        out["source"] = "command"
        if res.error:
            out["error"] = res.error
            if speak_reply:
                say(prof.get("error", "Не вышло."), profile=pid)
        elif speak_reply:
            if res.reply:
                say(res.reply, profile=pid)
            elif ok_reply:
                say(prof.get("ok", "Готово."), profile=pid)
        _write_last(out)
        return out

    # не команда — спросить ИИ (как Luxify AI)
    if speak_reply:
        say(prof.get("ask", "Секунду."), profile=pid)
    answer = jc._agent_answer(text)
    if answer.startswith("(") and answer.endswith(")"):
        out["error"] = answer
        if speak_reply:
            say("Не понял команду, сэр.", profile=pid)
    else:
        out["source"] = "ai"
        out["ok"] = True
        out["reply"] = answer
        if speak_reply:
            say(answer, profile=pid)
    _write_last(out)
    return out


def _write_last(out: dict) -> None:
    try:
        os.makedirs(VOICE_DIR, exist_ok=True)
        data = dict(out)
        data["t"] = datetime.now().strftime("%H:%M:%S")
        with open(LAST_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass
    _log(f"heard «{out['heard'][:120]}» -> {out['source']} "
         f"{'ok' if out['ok'] else 'fail'} {out.get('reply', '')[:80]}")


def listen_once(seconds: float = 5.0, speak_reply: bool = True,
                profile: Optional[str] = None,
                ok_reply: bool = True) -> dict:
    """Послушать микрофон один раз и обработать услышанное."""
    pcm = record(seconds)
    level = rms(pcm)
    text, backend = recognize(pcm)
    _log(f"слушал {seconds:g} сек (уровень {level:.0f}): «{text}» "
         f"[{backend}]")
    if not text:
        out = {"heard": "", "source": "none", "ok": False,
               "error": "не расслышал (тишина или непонятная речь)"}
        _write_last(out)
        return out
    return handle_text(text, speak_reply=speak_reply, profile=profile,
                       ok_reply=ok_reply)


# --------------------------------------------------------------------------- #
#  Демон: слушать в фоне
# --------------------------------------------------------------------------- #

def _log(line: str) -> None:
    try:
        os.makedirs(VOICE_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def _read_pid() -> Optional[int]:
    try:
        with open(PID_PATH, encoding="utf-8") as f:
            return int((f.read() or "0").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                           capture_output=True, timeout=10)
        out = (r.stdout or b"").decode("cp866", "replace")
        return "INFO:" not in out and str(pid) in out
    except Exception:  # noqa: BLE001
        return False


def daemon_running() -> bool:
    return _pid_alive(_read_pid())


def stop_daemon() -> bool:
    os.makedirs(VOICE_DIR, exist_ok=True)
    try:
        with open(STOP_PATH, "w", encoding="utf-8") as f:
            f.write("stop")
    except OSError:
        pass
    pid = _read_pid()
    if not _pid_alive(pid):
        return False
    # даём демону спокойно завершиться (он проверяет файл stop)
    deadline = time.time() + 8
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.4)
    if _pid_alive(pid):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=15)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.remove(PID_PATH)
    except OSError:
        pass
    return True


def daemon(wake: bool = True, chunk: float = 4.0,
           profile: Optional[str] = None, greeting: bool = True,
           max_minutes: float = 0.0) -> int:
    """Живой цикл: слушает и выполняет. Остановка — jarvis_voice.py stop."""
    if daemon_running():
        print("Демон уже запущен (или остался pid-файл). Сначала: stop")
        return 1
    try:
        os.remove(STOP_PATH)
    except OSError:
        pass
    os.makedirs(VOICE_DIR, exist_ok=True)
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    pid, prof = get_profile(profile)
    _log(f"демон запущен (голос {pid}, wake={wake}, chunk={chunk:g})")
    if greeting:
        say(prof.get("greet", "Слушаю."), profile=pid)
    started = time.time()
    silence_streak = 0
    try:
        while True:
            if os.path.exists(STOP_PATH):
                _log("найден файл stop — завершаюсь")
                break
            if max_minutes and (time.time() - started) > max_minutes * 60:
                _log("достигнут лимит времени — завершаюсь")
                break
            try:
                pcm = record(chunk)
            except VoiceError as e:
                _log(f"микрофон: {e}")
                time.sleep(2)
                continue
            level = rms(pcm)
            if level < MIN_RMS:
                silence_streak += 1
                if silence_streak == 20:
                    _log("тишина давно — жду дальше")
                    silence_streak = 0
                continue
            silence_streak = 0
            text, backend = recognize(pcm)
            if not text:
                continue
            _log(f"услышал [{backend}]: «{text}»")
            has_wake, rest = find_wake(text)
            if wake and not has_wake:
                continue
            phrase = rest if has_wake else text
            if has_wake and not phrase:
                say("Слушаю.", profile=pid)
                try:
                    pcm2 = record(4.5)
                    phrase = recognize(pcm2)[0]
                    _log(f"после имени: «{phrase}»")
                except VoiceError:
                    continue
                if not phrase:
                    continue
            handle_text(phrase, speak_reply=True, profile=pid)
    except KeyboardInterrupt:
        _log("остановлен с клавиатуры")
    finally:
        try:
            os.remove(PID_PATH)
        except OSError:
            pass
        try:
            os.remove(STOP_PATH)
        except OSError:
            pass
        _log("демон остановлен")
    return 0


# --------------------------------------------------------------------------- #
#  Статус и самопроверка
# --------------------------------------------------------------------------- #

def status(full: bool = False) -> dict:
    ok_v, v_reason = vosk_available()
    profs = load_profiles()
    st = {
        "mic": mic_info(),
        "asr_mode": asr_mode(),
        "vosk": {"available": ok_v, "info": v_reason},
        "google": {"available": True,
                   "info": "онлайн (ключ не нужен)"},
        "whisper": {"available": bool(os.environ.get("OPENAI_API_KEY")),
                    "info": "OpenAI Whisper (нужен OPENAI_API_KEY)"},
        "edge_tts": {"available": edge_available(),
                     "info": "голоса персонажей (pip install edge-tts)"},
        "profile_default": profs.get("_default", "jarvis"),
        "profiles": {k: v.get("name", k) for k, v in profs.items()
                     if not k.startswith("_")},
        "daemon": {"running": daemon_running(), "pid": _read_pid()},
    }
    if full:
        st["sapi_recognizers"] = sapi_recognizers()
    return st


def selftest() -> int:
    ok = fail = skipped = 0

    def check(cond: bool, what: str, skip: bool = False) -> None:
        nonlocal ok, fail, skipped
        if skip:
            skipped += 1
            print(f"  [skip] {what}")
        elif cond:
            ok += 1
            print(f"  [ok] {what}")
        else:
            fail += 1
            print(f"  [FAIL] {what}")

    print("1. Микрофон и звук")
    import jarvis_commands as jc
    mi = mic_info()
    check(bool(mi.get("available")), f"микрофон: {mi.get('default') or mi.get('error')}")
    check(edge_available() or True, "edge-tts", skip=not edge_available())

    print("2. Распознавание речи")
    ok_v, info = vosk_available()
    check(ok_v, f"vosk (офлайн): {info}", skip=not ok_v)
    sapi = sapi_recognizers()
    if sapi:
        check(True, f"Windows SAPI распознаватель: {', '.join(sapi)}")
    else:
        check(False, "Windows SAPI распознаватель (офлайн) — поставьте "
                     "русский речевой пакет: Параметры -> Язык и регион -> "
                     "Речь", skip=True)
    # онлайн-тест: синтезируем фразу голосом SAPI (TTS) и пробуем распознать
    pcm = _sapi_tts_wav_pcm("hello world this is jarvis, text to speech test")
    if pcm:
        text, backend = recognize(pcm, lang="en-US")
        check(bool(text) and backend in ("google", "whisper", "vosk"),
              f"онлайн-распознавание (слух работает): «{text}» [{backend}]")
    else:
        check(False, "онлайн-распознавание: не удалось синтезировать пробу "
                     "(нет ни одного SAPI-голоса)", skip=True)

    print("3. Голос (синтез)")
    _pid, _prof = get_profile("jarvis")
    path = synthesize("Проверка связи, сэр.", _prof, _pid)
    check(bool(path), f"синтез фразы: {path}")

    print("4. Понимание команд")
    heard = "джарвис, сделай потише"
    wake, rest = find_wake(heard)
    check(wake and rest == "сделай потише", "wake word «Джарвис»")
    lib = jc.Library()
    ms = jc.match_phrase(rest, lib)
    check(bool(ms) and "Тише" in ms[0].command.name,
          "команда «сделай потише» (пак windows стоит?)")
    res = jc.run_phrase("открой ютуб и сделай тише",
                        jc.ExecContext(dry_run=True), lib)
    check(res.ok and len(res.matched) == 2, "цепочка «открой ютуб и тише»")

    print(f"\nИтог: {ok} ок, {fail} проблем, {skipped} пропущено.")
    return 0 if fail == 0 else 1


def _sapi_tts_wav_pcm(text: str) -> Optional[bytes]:
    """Синтез пробы через SAPI в PCM16 16к (для онлайн-проверки слуха)."""
    import tempfile
    wav = os.path.join(tempfile.gettempdir(), "jarvis_asr_probe.wav")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{wav}', $f); "
        f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}'); $s.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=40)
        with open(wav, "rb") as f:
            data = f.read()
        return data[44:] if len(data) > 44 else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="JARVIS: голос (слух и синтез)")
    sub = ap.add_subparsers(dest="cmd")

    p_st = sub.add_parser("status", help="что доступно")
    p_st.add_argument("--full", action="store_true")

    p_once = sub.add_parser("once", help="послушать и выполнить один раз")
    p_once.add_argument("--sec", type=float, default=5.0)
    p_once.add_argument("--silent", action="store_true",
                        help="не отвечать голосом")
    p_once.add_argument("--profile", default="")

    p_list = sub.add_parser("listen", help="только распознать (без команд)")
    p_list.add_argument("--sec", type=float, default=5.0)

    p_say = sub.add_parser("say", help="озвучить текст")
    p_say.add_argument("text")
    p_say.add_argument("--profile", default="")

    p_d = sub.add_parser("daemon", help="слушать в фоне (wake word)")
    p_d.add_argument("--no-wake", action="store_true",
                     help="без имени «Джарвис» — реагировать на любую речь")
    p_d.add_argument("--chunk", type=float, default=4.0)
    p_d.add_argument("--profile", default="")
    p_d.add_argument("--minutes", type=float, default=0.0,
                     help="сколько минут слушать (0 — без ограничения)")

    sub.add_parser("stop", help="остановить демон")

    p_pr = sub.add_parser("profiles", help="голоса")
    p_pr.add_argument("--set", default="")

    p_ask = sub.add_parser("ask", help="спросить ИИ и озвучить")
    p_ask.add_argument("text")
    p_ask.add_argument("--profile", default="")

    sub.add_parser("selftest", help="самопроверка")

    a = ap.parse_args(argv)
    action = a.cmd or "status"

    if action == "status":
        print(json.dumps(status(full=a.full if hasattr(a, "full") else False),
                         ensure_ascii=False, indent=2))
        return 0
    if action == "listen":
        pcm = record(a.sec)
        text, backend = recognize(pcm)
        print(json.dumps({"text": text, "backend": backend,
                          "level": round(rms(pcm), 1)},
                         ensure_ascii=False, indent=2))
        return 0 if text else 1
    if action == "once":
        res = listen_once(a.sec, speak_reply=not a.silent,
                          profile=a.profile or None)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1
    if action == "say":
        print("ok" if say(a.text, profile=a.profile or None) else "не вышло")
        return 0
    if action == "daemon":
        return daemon(wake=not a.no_wake, chunk=a.chunk,
                      profile=a.profile or None, max_minutes=a.minutes)
    if action == "stop":
        print("остановлен" if stop_daemon() else "демон не запущен")
        return 0
    if action == "profiles":
        if a.set:
            try:
                print(json.dumps(set_default_profile(a.set),
                                 ensure_ascii=False))
            except ValueError as e:
                print(str(e))
                return 1
            return 0
        profs = load_profiles()
        for pid, p in profs.items():
            if pid.startswith("_"):
                continue
            mark = "*" if pid == profs.get("_default") else " "
            print(f" {mark} {pid:8s} {p.get('name', pid):10s} "
                  f"greet: «{p.get('greet', '')}»")
        print("\n* — текущий. Сменить: profiles --set <id>")
        return 0
    if action == "ask":
        import jarvis_commands as jc
        pid, prof = get_profile(a.profile or None)
        say(prof.get("ask", "Секунду."), profile=pid)
        answer = jc._agent_answer(a.text)
        print(answer)
        if not (answer.startswith("(") and answer.endswith(")")):
            say(answer, profile=pid)
        return 0
    if action == "selftest":
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
