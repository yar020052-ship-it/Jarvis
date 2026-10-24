"""
Speech-to-Text engines for MARK XL.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""

import json
import os
import sys
import sysconfig
from pathlib import Path

import numpy as np


_CUDA_DLL_HANDLES: list[object] = []
_CUDA_ERROR_MARKERS = ("cuda", "cublas", "cudnn", "out of memory", "device-side")


def _is_cuda_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _CUDA_ERROR_MARKERS)


def _prepare_windows_cuda_dlls() -> None:
    """Expose CUDA/cuDNN DLLs from PyTorch's bundled NVIDIA packages to CT2."""
    if sys.platform != "win32":
        return

    candidates: list[Path] = []
    try:
        import torch

        candidates.append(Path(torch.__file__).resolve().parent / "lib")
        # Importing Torch registers its own DLL directories before CTranslate2
        # attempts to dynamically load cuBLAS and cuDNN.
        torch.cuda.is_available()
    except Exception:
        pass

    try:
        site_packages = Path(sysconfig.get_paths()["purelib"])
        for package in ("cublas", "cudnn"):
            root = site_packages / "nvidia" / package
            candidates.extend((root / "bin", root / "lib", root))
    except Exception:
        pass

    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    for directory in candidates:
        if not directory.is_dir() or not any(directory.glob("*.dll")):
            continue
        value = str(directory)
        if value.casefold() not in {entry.casefold() for entry in path_entries if entry}:
            path_entries.insert(0, value)
            os.environ["PATH"] = os.pathsep.join(path_entries)
        if hasattr(os, "add_dll_directory"):
            try:
                _CUDA_DLL_HANDLES.append(os.add_dll_directory(value))
            except OSError:
                pass


def _ctranslate2_cuda_available() -> bool:
    """Ask the inference runtime itself whether it can see a CUDA device."""
    _prepare_windows_cuda_dlls()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception as exc:
        print(f"[STT] CTranslate2 CUDA unavailable; using CPU ({exc})")
        return False


class WhisperSTT:
    """Offline transcription using faster-whisper."""

    def __init__(self, model_name: str = "base", language: str | None = "ru"):
        _prepare_windows_cuda_dlls()
        from faster_whisper import WhisperModel

        print(f"[STT] Loading Whisper '{model_name}'…")
        device = "cuda" if _ctranslate2_cuda_available() else "cpu"
        compute = "float16" if device == "cuda" else "int8"

        def _load():
            return WhisperModel(model_name, device=device, compute_type=compute)

        def _load_cpu():
            return WhisperModel(model_name, device="cpu", compute_type="int8")

        self._model_factory = _load_cpu

        load_error = None
        try:
            self._model = _load()
        except Exception as first_error:
            if device == "cuda":
                print(
                    f"[STT] CUDA Whisper initialization failed; retrying on CPU ({first_error})"
                )
                device, compute = "cpu", "int8"
                try:
                    self._model = _load()
                except Exception as _cpu_err:
                    load_error = _cpu_err
            else:
                load_error = first_error

        if load_error is not None:
            # Offline flag set but model not cached yet → clear flags and download once.
            # Keywords cover multiple huggingface_hub error message variants across versions.
            _e = str(load_error).lower()
            _offline_keywords = (
                "offline",
                "not found",
                "cache",
                "localentry",
                "does not exist",
                "outgoing",
                "local_files_only",
            )
            if any(k in _e for k in _offline_keywords):
                print(
                    f"[STT] Whisper '{model_name}' not in local cache — downloading (one-time, internet required)…"
                )
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_DATASETS_OFFLINE", None)
                try:
                    self._model = _load()
                except Exception as _dl_err:
                    raise RuntimeError(
                        f"Whisper '{model_name}' model download failed.\n"
                        f"Internet access is required the first time to download the speech model (~75–290 MB).\n"
                        f"After the first download it runs fully offline.\n"
                        f"Details: {_dl_err}"
                    ) from _dl_err
            else:
                raise load_error

        self._language = (
            None
            if (not language or language.strip().lower() == "auto")
            else language.strip().lower()
        )
        self._device = device
        print(f"[STT] Whisper '{model_name}' ready ({device})")

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string."""
        try:
            return self._transcribe_on_current_device(audio)
        except Exception as e:
            print(f"[STT] Transcription error: {e}")
            if self._device == "cuda" and _is_cuda_error(e):
                print("[STT] CUDA transcription failed; switching Whisper to CPU.")
                try:
                    # The model weights are already cached from the CUDA load.
                    cpu_model = self._model_factory()
                    self._model = cpu_model
                    self._device = "cpu"
                    return self._transcribe_on_current_device(audio)
                except Exception as cpu_error:
                    print(f"[STT] CPU fallback failed: {cpu_error}")
                    raise cpu_error from e
            raise

    def _transcribe_on_current_device(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=1,  # greedy — 2-3x faster
            best_of=1,
            condition_on_previous_text=False,  # no hallucinations, faster
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return " ".join(s.text for s in segments).strip()


class VoskSTT:
    """Streaming transcription using Vosk."""

    def __init__(self, model_path: str | None = None, language: str = "ru"):
        from vosk import Model, KaldiRecognizer

        print("[STT] Loading Vosk model…")
        if model_path:
            model = Model(model_path)
        else:
            lang = (
                language.strip().lower()
                if language and language.strip().lower() != "auto"
                else "ru"
            )
            model = Model(lang=lang)
        self._rec = KaldiRecognizer(model, 16000)
        print("[STT] Vosk ready.")

    def process_chunk(self, audio_bytes: bytes) -> tuple[str, bool]:
        """Feed raw int16 LE PCM bytes. Returns (text, is_final)."""
        if self._rec.AcceptWaveform(audio_bytes):
            result = json.loads(self._rec.Result())
            return result.get("text", ""), True
        partial = json.loads(self._rec.PartialResult())
        return partial.get("partial", ""), False
