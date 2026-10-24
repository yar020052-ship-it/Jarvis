from core import plugin_loader
from core.plugin_loader import PluginRecord, PluginRegistry


def test_plugin_dispatch_and_runtime_diagnostics(monkeypatch):
    monkeypatch.setattr(plugin_loader, "get_plugin_enabled", lambda _name: True)
    messages = []
    notices = []

    def run(parameters, player=None):
        if parameters.get("fail"):
            raise ValueError("sample plugin failure")
        return f"{parameters['message']}:{player}"

    record = PluginRecord(name="sample", run=run, valid=True, description="test")
    registry = PluginRegistry({"sample": record}, messages.append, notices.append)

    assert registry.run("sample", {"message": "hello"}, player="HUD") == "hello:HUD"
    assert registry.run("sample", {"fail": True}) == "Sir, the 'sample' plugin failed: sample plugin failure"
    assert registry.diagnostics()["runtime_errors"] == [
        {"name": "sample", "error": "sample plugin failure"}
    ]
    assert "sample" in notices[0]
    assert "sample plugin failure" in messages[0]
