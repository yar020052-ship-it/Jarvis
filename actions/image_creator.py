"""Generate or edit an image with the configured Gemini API key."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from memory.config_manager import get_gemini_key

_MODEL = "gemini-3.1-flash-image"
_MAX_INPUT_BYTES = 20 * 1024 * 1024
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def image_creator(parameters: dict, player=None) -> str:
    """Create a new image, or edit an explicitly provided image, then preview it."""
    p = parameters if isinstance(parameters, dict) else {}
    prompt = str(p.get("prompt") or "").strip()
    if not prompt:
        return "Опишите, какое изображение нужно создать или как изменить исходную картинку."
    if len(prompt) > 6000:
        return "Описание слишком длинное (максимум 6000 символов)."

    key = get_gemini_key()
    if not key:
        return "Для генерации изображений сначала добавьте Gemini API key в настройки JARVIS."

    source_path = str(p.get("image_path") or "").strip()
    contents: list = [prompt]
    if source_path:
        source = Path(os.path.expandvars(os.path.expanduser(source_path))).resolve()
        if source.suffix.lower() not in _IMAGE_EXTENSIONS or not source.is_file():
            return "Для редактирования укажите существующее изображение PNG, JPG или WebP."
        if source.stat().st_size > _MAX_INPUT_BYTES:
            return "Исходная картинка больше 20 МБ; уменьшите её перед редактированием."
        try:
            from PIL import Image
            with Image.open(source) as image:
                contents.append(image.copy())
        except Exception as exc:
            return f"Не удалось прочитать исходное изображение: {exc}"

    try:
        from google.genai import types
        from core import gemini

        client = gemini.client(
            timeout_ms=90_000,
            key=key,
            profile="image",
        )
        response = client.models.generate_content(
            model=_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
    except Exception as exc:
        return f"Генерация изображения не удалась: {exc}"

    image = None
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "inline_data", None) is not None:
                try:
                    image = part.as_image()
                    break
                except Exception:
                    continue
        if image is not None:
            break
    if image is None:
        text = str(getattr(response, "text", "") or "").strip()
        return "Модель не вернула файл изображения." + (f" Ответ модели: {text[:500]}" if text else "")

    try:
        from core.paths import get_user_data_dir
        output_dir = get_user_data_dir() / "generated_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"jarvis_{uuid.uuid4().hex[:10]}.png"
        image.save(output_path, format="PNG")
    except Exception as exc:
        return f"Изображение создано, но не удалось сохранить файл: {exc}"

    if player and hasattr(player, "show_image"):
        try:
            player.show_image("СОЗДАННОЕ ИЗОБРАЖЕНИЕ", str(output_path))
        except Exception:
            pass
    return f"Готово. Изображение сохранено: {output_path}"


TOOL = {
    "name": "image_creator",
    "description": (
        "Create a new image from a text prompt, or edit a provided image using the prompt. "
        "The result is saved in JARVIS user data and shown in the app preview. "
        "Use this for requests to draw, generate, make, or edit an image. A Gemini API key "
        "must be configured. For edits, pass the uploaded image path when available."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "Detailed image generation or editing instructions."},
            "image_path": {"type": "STRING", "description": "Optional path to an existing PNG, JPG or WebP image to edit."},
        },
        "required": ["prompt"],
    },
    "handler": image_creator,
}
