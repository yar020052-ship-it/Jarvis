import os

from core import i18n


def test_translation_fallback_and_formatting():
    i18n.set_language("en")
    assert i18n.t("missing.key", default="fallback {x}", x=7) == "fallback 7"
    i18n.set_language("ru")
    assert "ошиб" in i18n.t("activity.errors", count=3)


def test_available_languages_include_builtins():
    assert "ru" in i18n.available_languages()
    assert "en" in i18n.available_languages()