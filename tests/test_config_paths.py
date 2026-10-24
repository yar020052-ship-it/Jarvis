from __future__ import annotations

import json

import pytest

from core import paths


def _set_config_roots(tmp_path, monkeypatch):
    user_dir = tmp_path / "user-data" / "config"
    app_dir = tmp_path / "application"
    monkeypatch.setattr(paths, "get_config_dir", lambda: user_dir)
    monkeypatch.setattr(paths, "get_app_dir", lambda: app_dir)
    return user_dir, app_dir


def test_user_config_wins_over_legacy_application_config(tmp_path, monkeypatch):
    user_dir, app_dir = _set_config_roots(tmp_path, monkeypatch)
    (app_dir / "config").mkdir(parents=True)
    user_dir.mkdir(parents=True)
    (app_dir / "config" / "api_keys.json").write_text(
        json.dumps({"assistant_name": "Old", "gemini_api_key": "legacy-key"}),
        encoding="utf-8",
    )
    (user_dir / "api_keys.json").write_text(
        json.dumps({"assistant_name": "Current", "gemini_api_key": "user-key"}),
        encoding="utf-8",
    )

    assert paths.get_config_path("api_keys.json") == user_dir / "api_keys.json"
    assert paths.load_config("api_keys.json")["assistant_name"] == "Current"


def test_legacy_config_is_used_until_user_copy_exists(tmp_path, monkeypatch):
    user_dir, app_dir = _set_config_roots(tmp_path, monkeypatch)
    legacy_path = app_dir / "config" / "api_keys.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(json.dumps({"gemini_live_model": "legacy-model"}), encoding="utf-8")

    assert paths.get_config_path("api_keys.json") == legacy_path
    assert paths.load_config("api_keys.json") == {"gemini_live_model": "legacy-model"}

    user_dir.mkdir(parents=True)
    (user_dir / "api_keys.json").write_text(json.dumps({"assistant_name": "JARVIS"}), encoding="utf-8")
    assert paths.get_config_path("api_keys.json") == user_dir / "api_keys.json"
    assert paths.load_config("api_keys.json") == {"assistant_name": "JARVIS"}


def test_optional_legacy_merge_preserves_user_values(tmp_path, monkeypatch):
    user_dir, app_dir = _set_config_roots(tmp_path, monkeypatch)
    user_dir.mkdir(parents=True)
    (app_dir / "config").mkdir(parents=True)
    (app_dir / "config" / "api_keys.json").write_text(
        json.dumps({"gemini_live_model": "legacy-model", "assistant_name": "Old"}),
        encoding="utf-8",
    )
    (user_dir / "api_keys.json").write_text(
        json.dumps({"assistant_name": "Current"}), encoding="utf-8"
    )

    assert paths.load_config("api_keys.json") == {"assistant_name": "Current"}
    assert paths.load_config("api_keys.json", include_legacy=True) == {
        "gemini_live_model": "legacy-model",
        "assistant_name": "Current",
    }


def test_corrupt_user_config_does_not_fall_back_to_old_secrets(tmp_path, monkeypatch):
    user_dir, app_dir = _set_config_roots(tmp_path, monkeypatch)
    user_dir.mkdir(parents=True)
    (app_dir / "config").mkdir(parents=True)
    (user_dir / "api_keys.json").write_text("{broken", encoding="utf-8")
    (app_dir / "config" / "api_keys.json").write_text(
        json.dumps({"gemini_api_key": "legacy-key"}), encoding="utf-8"
    )

    assert paths.load_config("api_keys.json") == {}


@pytest.mark.parametrize("name", ["../api_keys.json", "..\\api_keys.json", "C:api_keys.json"])
def test_config_name_cannot_escape_config_directory(name):
    with pytest.raises(ValueError):
        paths.get_config_path(name)


def test_live_session_reads_the_active_user_api_key(monkeypatch):
    import main

    monkeypatch.setattr(main, "load_config", lambda _name: {"gemini_api_key": "user-key"})
    assert main._get_api_key() == "user-key"


def test_gemini_key_cache_refreshes_after_user_settings_change(tmp_path, monkeypatch):
    from core import gemini

    user_dir, app_dir = _set_config_roots(tmp_path, monkeypatch)
    user_dir.mkdir(parents=True)
    (app_dir / "config").mkdir(parents=True)
    (app_dir / "config" / "api_keys.json").write_text(
        json.dumps({"gemini_api_key": "legacy-key"}), encoding="utf-8"
    )
    config = user_dir / "api_keys.json"
    config.write_text(json.dumps({"gemini_api_key": "first-user-key"}), encoding="utf-8")
    monkeypatch.setattr(gemini, "_cached_key", None)
    monkeypatch.setattr(gemini, "_cached_key_signature", None)

    assert gemini.api_key(refresh=True) == "first-user-key"
    config.write_text(json.dumps({"gemini_api_key": "updated-user-key"}), encoding="utf-8")
    assert gemini.api_key() == "updated-user-key"
