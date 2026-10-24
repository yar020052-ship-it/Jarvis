"""Тесты извлечения JSON из ответов моделей (fence, проза, несколько объектов)."""

from core.jsonio import extract_json_object


def test_plain_object():
    assert extract_json_object('{"actions": []}') == {"actions": []}


def test_fenced_with_prose_before_and_after():
    text = (
        "Вот план кампании:\n"
        "```json\n"
        '{"phase": "рост", "actions": [{"do": "click"}]}\n'
        "```\n"
        "Готово."
    )
    assert extract_json_object(text) == {
        "phase": "рост",
        "actions": [{"do": "click"}],
    }


def test_fence_without_language_tag():
    assert extract_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_two_objects_returns_first():
    assert extract_json_object('{"a": 1}\n{"b": 2}') == {"a": 1}


def test_braces_inside_strings_do_not_break_parsing():
    text = '{"note": "используй {x} и \\"кавычки\\"", "ok": true}'
    assert extract_json_object(text) == {
        "note": 'используй {x} и "кавычки"',
        "ok": True,
    }


def test_prose_with_non_json_braces_before_object():
    text = 'Формат {ключ: значение}. Вот ответ: {"ok": true}'
    assert extract_json_object(text) == {"ok": True}


def test_nested_objects():
    text = '{"a": {"b": {"c": 1}}, "d": [1, 2]}'
    assert extract_json_object(text) == {"a": {"b": {"c": 1}}, "d": [1, 2]}


def test_bom_and_whitespace():
    assert extract_json_object('\ufeff  {"a": 1}\n') == {"a": 1}


def test_no_json_returns_none():
    assert extract_json_object("Модель ничего не вернула") is None
    assert extract_json_object("") is None


def test_truncated_json_returns_none():
    assert extract_json_object('{"a": 1, "b":') is None


def test_object_inside_array_is_found():
    # Модель иногда оборачивает план в массив — полезный объект всё равно нужен.
    assert extract_json_object('[{"a": 1}]') == {"a": 1}
