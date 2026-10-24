"""Optional, local-only estimates of vocal delivery from short audio clips.

The model output is not a reliable reading of a person's internal emotional
state. This module deliberately does not analyze images or webcam frames.
"""

from __future__ import annotations

import os
import tempfile
import wave

_voice_model = None
_voice_device = "cpu"

_LABEL_RU = {
    "angry": "злость", "anger": "злость", "disgusted": "отвращение",
    "disgust": "отвращение", "fearful": "страх", "fear": "страх",
    "happy": "радость", "happiness": "радость", "neutral": "нейтрально",
    "sad": "грусть", "sadness": "грусть", "surprised": "удивление",
    "surprise": "удивление", "contempt": "презрение",
    "other": "другое", "unknown": "неясно",
}


def label_ru(label: str) -> str:
    value = str(label).strip().lower()
    return _LABEL_RU.get(value, value)


def _load_voice_model(force_cpu: bool = False):
    global _voice_model, _voice_device
    if _voice_model is None:
        from funasr import AutoModel

        device = "cuda:0" if (not force_cpu and _cuda_available()) else "cpu"
        try:
            _voice_model = AutoModel(
                model="emotion2vec/emotion2vec_plus_large",
                hub="hf",
                device=device,
                disable_update=True,
            )
        except Exception as exc:
            if not device.startswith("cuda"):
                raise
            print(f"[Affect] CUDA model load failed; retrying on CPU ({exc})")
            _voice_model = AutoModel(
                model="emotion2vec/emotion2vec_plus_large",
                hub="hf",
                device="cpu",
                disable_update=True,
            )
            device = "cpu"
        _voice_device = device
    return _voice_model


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def analyze_voice(pcm_s16le: bytes, sample_rate: int = 16000):
    """Classify one short PCM utterance locally. Temporary WAV is always removed."""
    global _voice_model, _voice_device
    if not pcm_s16le or sample_rate <= 0:
        return None
    model = _load_voice_model()
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm_s16le)
        try:
            result = model.generate(
                input=path, granularity="utterance", extract_embedding=False
            )
        except Exception as exc:
            if not _voice_device.startswith("cuda"):
                raise
            print(f"[Affect] CUDA inference failed; retrying on CPU ({exc})")
            _voice_model = None
            model = _load_voice_model(force_cpu=True)
            result = model.generate(
                input=path, granularity="utterance", extract_embedding=False
            )
        item = result[0] if result else {}
        labels = item.get("labels")
        scores = item.get("scores")
        if labels is None or scores is None:
            return None
        import numpy as np

        if isinstance(labels, str):
            labels = [labels]
        else:
            labels = list(labels)
        scores = np.asarray(scores, dtype=np.float64).reshape(-1).tolist()
        if not labels or not scores:
            return None
        best = max(zip(labels, scores), key=lambda pair: float(pair[1]))
        return str(best[0]), float(best[1])
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

