from __future__ import annotations

import sys
import uuid

from core.action_loader import ActionRecord, ActionRegistry, discover_actions


def test_registry_passes_only_declared_context_and_records_recent_failures():
    messages = []

    def handler(parameters, player=None):
        return f"{parameters['value']}:{player}"

    registry = ActionRegistry(
        {"hello": ActionRecord(name="hello", handler=handler, valid=True)},
        messages.append,
    )
    assert registry.run("hello", {"value": "ok"}, {"player": "HUD", "speak": object()}) == "ok:HUD"

    def fail(parameters):
        raise RuntimeError(f"bad {parameters['index']}")

    registry._actions["fail"] = ActionRecord(name="fail", handler=fail, valid=True)
    for i in range(25):
        assert registry.run("fail", {"index": i}).startswith("Tool 'fail' failed:")

    diagnostics = registry.diagnostics()
    assert diagnostics["active"] == 2
    assert len(diagnostics["runtime_errors"]) == 20
    assert diagnostics["runtime_errors"][0]["error"] == "bad 5"
    assert diagnostics["runtime_errors"][-1]["error"] == "bad 24"
    assert len(messages) == 25


def test_discovery_keeps_good_actions_and_reports_bad_contract(tmp_path):
    token = uuid.uuid4().hex
    good_stem = f"contract_good_{token}"
    bad_stem = f"contract_bad_{token}"
    (tmp_path / f"{good_stem}.py").write_text(
        "def run(parameters):\n    return parameters['value']\n"
        "TOOL = {'name': 'contract_good', 'description': 'test', 'handler': run}\n",
        encoding="utf-8",
    )
    (tmp_path / f"{bad_stem}.py").write_text(
        "TOOL = {'name': 'contract_bad', 'description': '', 'handler': lambda p: p}\n",
        encoding="utf-8",
    )
    modules = (f"actions.{good_stem}", f"actions.{bad_stem}")

    registry = discover_actions(tmp_path, logger=lambda _message: None)
    try:
        assert registry.names() == {"contract_good"}
        assert registry.run("contract_good", {"value": "discovered"}) == "discovered"
        rejected = registry.diagnostics()["rejected"]
        assert len(rejected) == 1
        assert rejected[0]["name"] == "contract_bad"
        assert "description" in rejected[0]["error"]
    finally:
        for name in modules:
            sys.modules.pop(name, None)
