"""Voice cloning for JARVIS — text-to-speech with custom voices.

Supports external TTS services: ElevenLabs, Google Cloud TTS, Azure TTS.
Credentials stored in config/api_keys.json.
"""

import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _config() -> dict:
    try:
        p = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def speak_with_voice(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Generate speech with a custom voice."""
    params = parameters or {}
    text = params.get("text", "").strip()
    voice_id = params.get("voice_id", "").strip()
    service = params.get("service", "elevenlabs").lower()

    if not text:
        return "Укажите текст (text)."

    cfg = _config()

    if service == "elevenlabs":
        api_key = cfg.get("elevenlabs_api_key", "")
        if not api_key:
            return "ElevenLabs API ключ не настроен. Укажите elevenlabs_api_key в config/api_keys.json."
        vid = voice_id or cfg.get("elevenlabs_voice_id", "")
        if not vid:
            return "Укажите voice_id или elevenlabs_voice_id в config."

        try:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
            data = json.dumps({
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                audio_data = resp.read()
            # Save to temp file
            out = Path(__file__).resolve().parent.parent / "temp" / "cloned_voice.mp3"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(audio_data)
            return f"Аудио сгенерировано: {out} ({len(audio_data)} байт)"
        except Exception as e:
            return f"Ошибка ElevenLabs: {e}"

    elif service == "google":
        api_key = cfg.get("google_tts_api_key", "")
        if not api_key:
            return "Google TTS API ключ не настроен."
        try:
            url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
            data = json.dumps({
                "input": {"text": text},
                "voice": {"languageCode": "ru-RU", "name": voice_id or "ru-RU-Wavenet-D"},
                "audioConfig": {"audioEncoding": "MP3"},
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            audio = base64.b64decode(result.get("audioContent", ""))
            out = Path(__file__).resolve().parent.parent / "temp" / "google_tts.mp3"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(audio)
            return f"Аудио сгенерировано: {out} ({len(audio)} байт)"
        except Exception as e:
            return f"Ошибка Google TTS: {e}"

    return f"Неизвестный сервис: {service}. Поддерживается: elevenlabs, google"


def list_voices(parameters=None, response=None, player=None, session_memory=None) -> str:
    """List available voices for a service."""
    params = parameters or {}
    service = params.get("service", "elevenlabs").lower()
    cfg = _config()

    if service == "elevenlabs":
        api_key = cfg.get("elevenlabs_api_key", "")
        if not api_key:
            return "ElevenLabs API ключ не настроен."
        try:
            req = urllib.request.Request(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": api_key},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            voices = data.get("voices", [])
            return "Голоса ElevenLabs:\n" + "\n".join(
                f"  • {v.get('name')} (ID: {v.get('voice_id')})" for v in voices[:20]
            )
        except Exception as e:
            return f"Ошибка: {e}"

    return f"Список голосов для {service} не поддерживается."


TOOL = {
    "name": "voice_clone",
    "description": "Generate speech with custom/cloned voices via ElevenLabs or Google TTS.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "speak (generate speech) or voices (list available voices)",
            },
            "text": {
                "type": "STRING",
                "description": "Text to speak",
            },
            "voice_id": {
                "type": "STRING",
                "description": "Voice ID to use",
            },
            "service": {
                "type": "STRING",
                "description": "TTS service: elevenlabs or google (default: elevenlabs)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        speak_with_voice(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "speak").lower() == "speak"
        else list_voices(parameters, response, player, session_memory)
    ),
}