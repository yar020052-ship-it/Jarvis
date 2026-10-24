import importlib
import json


mcp_manager_module = importlib.import_module("actions.mcp_manager")


def test_human_policy_change_to_never_is_persisted(tmp_path, monkeypatch):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(json.dumps({"servers": [
        {"name": "telegram_personal", "enabled": True, "transport": "stdio", "approval": "writes"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(mcp_manager_module, "_config_path", lambda: config_path)
    monkeypatch.setattr(mcp_manager_module, "push_undo", lambda *_args: None)

    result = mcp_manager_module.mcp_manager(
        {"action": "set_approval", "name": "telegram_personal", "approval": "never"},
        _user_authorized=True,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert result.startswith("Saved MCP configuration")
    assert saved["servers"][0]["approval"] == "never"


def test_model_policy_change_to_never_waits_for_confirmation(tmp_path, monkeypatch):
    config_path = tmp_path / "mcp_servers.json"
    original = {"servers": [
        {"name": "telegram_personal", "enabled": True, "transport": "stdio", "approval": "writes"},
    ]}
    config_path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(mcp_manager_module, "_config_path", lambda: config_path)
    monkeypatch.setattr(mcp_manager_module, "push_undo", lambda *_args: None)
    monkeypatch.setattr(mcp_manager_module, "pending_title", lambda: False)
    pending = {}

    def request_confirmation(**kwargs):
        pending.update(kwargs)
        return "Waiting for on-screen confirmation."

    monkeypatch.setattr(mcp_manager_module, "request_confirmation", request_confirmation)
    result = mcp_manager_module.mcp_manager(
        {"action": "set_approval", "name": "telegram_personal", "approval": "never"},
    )
    assert result == "Waiting for on-screen confirmation."
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
    assert "WITHOUT asking" in pending["detail"]

    pending["run"]()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["servers"][0]["approval"] == "never"
