from types import SimpleNamespace

import numpy as np

import main as jarvis_main


def test_viseme_schedule_covers_audio_and_handles_silence():
    pcm = (np.sin(np.arange(4_800, dtype=np.float32) * (2 * np.pi * 440 / 24_000)) * 12_000).astype(np.int16)
    frames = jarvis_main._pcm_visemes(pcm)
    assert len(frames) == 10
    assert all(len(frame) == 3 for frame in frames)
    assert max(frame[0] for frame in frames) > 0
    assert 24_000 in jarvis_main._VIS_BANDS_CACHE

    silence = jarvis_main._pcm_visemes(np.zeros(4_800, dtype=np.int16))
    assert len(silence) == 10
    assert all(frame == (0.0, 0.0, 0.0) for frame in silence)
    assert jarvis_main._pcm_visemes(np.ones(4_800, dtype=np.int16), sr=0) == []


def test_module_status_report_includes_live_failures_and_history(monkeypatch):
    from autonomy import health, incidents
    from autonomy.health import Check, HealthReport

    monkeypatch.setattr(health, "run_health", lambda: HealthReport([Check("syntax", True, 2, "файлов: 180")]))
    monkeypatch.setattr(jarvis_main, "get_system_status", lambda: {
        "cpu_percent": 23.5,
        "ram_percent": 50.0,
        "gpu_percent": 4.0,
        "temperature_c": 60.0,
        "temperature_kind": "GPU",
        "uptime": "2h 30m",
    })
    monkeypatch.setattr(incidents, "open_items", lambda: [{
        "id": "open-1", "status": "open", "where": "actions/test.py", "error": "current failure",
    }])
    monkeypatch.setattr(incidents, "all_items", lambda _limit: [
        {"id": "failed-1", "status": "failed", "where": "plugins/old.py", "error": "repair attempt failed"},
        {"id": "fixed-1", "status": "fixed", "where": "actions/fixed.py", "error": "old failure"},
    ])

    jarvis = SimpleNamespace(
        session=object(),
        _awake=True,
        _action_registry=SimpleNamespace(diagnostics=lambda: {
            "active": 50,
            "rejected": [{"name": "broken", "file": "broken.py", "error": "missing handler"}],
            "runtime_errors": [{"name": "crashed", "error": "runtime boom"}],
        }),
        _plugin_registry=SimpleNamespace(diagnostics=lambda: {
            "active": 10, "loaded": 11, "disabled": ["off"], "rejected": [], "runtime_errors": [],
        }),
        _mcp_registry=SimpleNamespace(status=lambda: "MCP configuration\nserver: 2 tool(s)"),
        ui=SimpleNamespace(
            muted=True,
            _activity_error_count=1,
            activity_error_events=lambda: ["ERROR: Telegram connection lost"],
        ),
    )

    report = jarvis_main.JarvisLive._module_status_report(jarvis)
    assert "ошибок загрузки 1" in report
    assert "runtime boom" in report
    assert "repair attempt failed" in report
    assert "Статус failed в истории не доказывает" in report
    assert "Telegram connection lost" in report
    assert "Голосовой диалог: живая сессия подключена, микрофон отключён" in report
    assert "Маршрут ИИ: models/gemini-3.8-live" in report
    assert "CPU 23.5%, RAM 50.0%, GPU 4.0%, температура GPU 60.0°C" in report
    assert "ЕСТЬ ПРОБЛЕМЫ" not in report  # historical failures aren't current health failures


def test_module_status_report_survives_and_reports_failed_diagnostics(monkeypatch):
    from autonomy import health, incidents

    def unavailable():
        raise RuntimeError("diagnostic source failed")

    monkeypatch.setattr(health, "run_health", unavailable)
    monkeypatch.setattr(incidents, "open_items", unavailable)
    monkeypatch.setattr(incidents, "all_items", lambda _limit: unavailable())
    monkeypatch.setattr(jarvis_main, "get_system_status", unavailable)
    jarvis = SimpleNamespace(
        session=None,
        _action_registry=SimpleNamespace(diagnostics=unavailable),
        _plugin_registry=SimpleNamespace(diagnostics=unavailable),
        _mcp_registry=SimpleNamespace(status=unavailable),
        ui=SimpleNamespace(
            _activity_error_count=0,
            activity_error_events=unavailable,
        ),
    )

    report = jarvis_main.JarvisLive._module_status_report(jarvis)

    assert "Голосовой диалог: живая сессия не подключена" in report
    assert "Система: статус недоступен (RuntimeError: diagnostic source failed)" in report
    assert "Действия: диагностика недоступна" in report
    assert "Плагины: диагностика недоступна" in report
    assert "MCP: статус недоступен" in report
    assert "Проверка кода: статус недоступен" in report
    assert "Инциденты: статус недоступен" in report
    assert "Журнал ошибок: статус недоступен" in report
    assert "ошибок загрузки 0" not in report


def test_settings_policy_change_to_never_persists_and_applies_immediately(tmp_path, monkeypatch):
    import json
    from actions import mcp_manager as manager

    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(json.dumps({"servers": [
        {"name": "telegram_personal", "enabled": True, "transport": "stdio", "approval": "writes"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(manager, "_config_path", lambda: config_path)
    monkeypatch.setattr(manager, "push_undo", lambda *_args: None)

    def unexpected_confirmation(**_kwargs):
        raise AssertionError("A direct settings selection must not ask the model to confirm it.")

    monkeypatch.setattr(manager, "request_confirmation", unexpected_confirmation)
    approvals = []
    messages = []
    jarvis = SimpleNamespace(
        _mcp_registry=SimpleNamespace(set_server_approval=lambda name, policy: approvals.append((name, policy))),
        ui=SimpleNamespace(write_log=messages.append),
    )

    saved = jarvis_main.JarvisLive._ui_mcp_policy_change(jarvis, "telegram_personal", "never")

    assert saved is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["servers"][0]["approval"] == "never"
    assert approvals == [("telegram_personal", "never")]
    assert messages and "сохранена и действует сразу" in messages[0]


def test_telegram_updates_persist_only_checkpoint_ids_in_user_data(tmp_path, monkeypatch):
    from core import paths

    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)
    payloads = [
        {
            "messages": [{"chat": "Alex", "message_id": 12, "text": "private test text"}],
            "errors": [],
            "checkpoints": {"2": {"title": "Alex", "last_message_id": 12}},
            "first_run": True,
        },
        {
            "messages": [],
            "errors": [],
            "checkpoints": {"2": {"title": "Alex", "last_message_id": 12}},
            "first_run": False,
        },
    ]
    calls = []

    class FakeMCP:
        def has(self, _name):
            return True

        def call(self, _name, arguments, **_kwargs):
            calls.append(arguments)
            return SimpleNamespace(text=__import__("json").dumps(payloads.pop(0)))

    jarvis = SimpleNamespace(_mcp_registry=FakeMCP())
    first = jarvis_main.JarvisLive._collect_telegram_updates(jarvis)
    checkpoint = tmp_path / "mcp" / "telegram-personal" / "briefing_state.json"
    saved = __import__("json").loads(checkpoint.read_text(encoding="utf-8"))
    assert first["messages"][0]["text"] == "private test text"
    assert saved == {"chats": {"2": {"title": "Alex", "last_message_id": 12}}}

    second = jarvis_main.JarvisLive._collect_telegram_updates(jarvis)
    assert __import__("json").loads(calls[1]["since_json"]) == {"2": 12}
    assert second["messages"] == []
    assert second["first_run"] is False
