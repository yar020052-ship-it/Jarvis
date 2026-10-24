from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon.tl.types import User

from mcp_servers.telegram_personal import server


def test_stdio_server_imports_when_started_as_a_script():
    project_root = Path(server.__file__).resolve().parents[2]
    script = Path(server.__file__).resolve()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=project_root,
        input="",
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )

    assert result.returncode == 0, result.stderr[-1200:]
    assert "ModuleNotFoundError" not in result.stderr


class FakeMessage:
    def __init__(self, message_id, text, sender, *, outgoing=False):
        self.id = message_id
        self.message = text
        self.date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.out = outgoing
        self.action = None
        self.sender = sender
        self.reply_to = None
        self.reply_to_msg_id = None

    async def get_sender(self):
        return self.sender


class FakeClient:
    def __init__(self, dialogs, messages):
        self.dialogs = dialogs
        self.messages = messages
        self.owner = User(id=1, is_self=True, bot=False, first_name="Owner", username="owner")

    async def get_me(self):
        return self.owner

    async def iter_dialogs(self, limit=100):
        for dialog in self.dialogs[:limit]:
            yield dialog

    async def iter_messages(self, entity, limit=20):
        for message in self.messages.get(entity.id, [])[:limit]:
            yield message

    async def download_profile_photo(self, *_args, **_kwargs):
        return None


def _dialog(entity, title, unread, latest_id):
    return SimpleNamespace(
        id=entity.id,
        entity=entity,
        name=title,
        unread_count=unread,
        message=SimpleNamespace(id=latest_id),
    )


def test_dm_filter_and_incremental_updates_only_return_new_human_messages(monkeypatch):
    person = User(id=2, is_self=False, bot=False, first_name="Alex")
    bot = User(id=3, is_self=False, bot=True, first_name="Helper")
    assert server._is_personal_dm(person)
    assert not server._is_personal_dm(bot)
    assert not server._is_personal_dm(FakeClient([], {}).owner)

    dialogs = [_dialog(person, "Alex", unread=3, latest_id=12), _dialog(bot, "Helper", unread=1, latest_id=90)]
    initial_client = FakeClient(dialogs, {
        2: [
            FakeMessage(12, "newest", person),
            FakeMessage(11, "middle", person),
            FakeMessage(10, "already read", person),
            FakeMessage(9, "outgoing", person, outgoing=True),
        ],
        3: [FakeMessage(90, "bot update", bot)],
    })
    async def use_initial_client():
        return initial_client

    monkeypatch.setattr(server, "_get_client", use_initial_client)
    monkeypatch.setattr(server, "_load_read_channels", lambda: (set(), set()))
    monkeypatch.setattr(server, "_load_read_groups", lambda: (set(), set()))

    first = json.loads(asyncio.run(server.telegram_read_updates(since_json="{}")))
    assert first["first_run"] is True
    assert [(row["message_id"], row["text"]) for row in first["messages"]] == [
        (11, "middle"), (12, "newest"),
    ]
    assert first["checkpoints"]["2"]["last_message_id"] == 12
    assert "3" not in first["checkpoints"]
    assert first["errors"] == []

    newer_client = FakeClient(
        [_dialog(person, "Alex", unread=1, latest_id=13)],
        {2: [FakeMessage(13, "one new message", person), FakeMessage(12, "newest", person)]},
    )
    async def use_newer_client():
        return newer_client

    monkeypatch.setattr(server, "_get_client", use_newer_client)
    since = json.dumps({key: row["last_message_id"] for key, row in first["checkpoints"].items()})
    second = json.loads(asyncio.run(server.telegram_read_updates(since_json=since)))
    assert second["first_run"] is False
    assert [(row["message_id"], row["text"]) for row in second["messages"]] == [(13, "one new message")]
    assert second["checkpoints"]["2"]["last_message_id"] == 13


def test_invalid_checkpoint_input_returns_a_clear_error(monkeypatch):
    client = FakeClient([], {})

    async def use_client():
        return client

    monkeypatch.setattr(server, "_get_client", use_client)
    result = json.loads(asyncio.run(server.telegram_read_updates(since_json="not-json")))
    assert "since_json" in result["error"]


def test_group_and_channel_allowlists_read_the_shared_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_DIR", tmp_path)
    (tmp_path / "api_keys.json").write_text(json.dumps({
        "telegram_read_channels": ["@news", "-100123"],
        "telegram_read_groups": ["@team", "-100456"],
    }), encoding="utf-8")

    channel_ids, channel_names = server._load_read_channels()
    group_ids, group_names = server._load_read_groups()
    assert channel_ids == {"-100123"}
    assert channel_names == {"news"}
    assert group_ids == {"-100456"}
    assert group_names == {"team"}
