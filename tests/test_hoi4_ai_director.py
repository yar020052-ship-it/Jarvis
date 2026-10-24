from __future__ import annotations

from pathlib import Path

import pytest

from actions import hoi4_ai_director as director


FAKE = {
    "observed_ids": {
        "equipment_production_factor": ["fighter", "cas", "anti_air", "anti_tank", "infantry"],
        "equipment_variant_production_factor": ["small_plane_cas_airframe"],
        "template_prio": ["infantry", "armor"],
        "build_building": ["arms_factory", "industrial_complex", "air_base", "radar_station"],
        "research_weight_factor": ["improved_infantry_weapons", "artillery4"],
        "air_factory_balance": [],
    },
    "country_coverage": {},
    "country_files": [],
    "types": {"equipment_production_factor": 3},
    "buildings": [],
    "ai_template_roles": [],
    "equipment_archetypes": [],
    "files": 1,
    "game_root": "",
    "mod_root": "",
    "mods_root": "",
}


def test_generated_strategy_is_valid_and_only_uses_known_ids():
    text, stats = director.build_strategy(FAKE)
    assert MARKER_LINE in text
    assert stats["problems"] == []
    assert stats["entries"] > 0
    assert "id = interceptor" not in text  # not present in the fake catalog
    assert "equipment_production_factor id = fighter" in text


def test_validate_rejects_unknown_ids_and_types():
    bad = (
        "x = {\n"
        "\tai_strategy = { type = equipment_production_factor id = spaceship value = 5 }\n"
        "\tai_strategy = { type = cheat_everything value = 5 }\n"
        "}\n"
    )
    problems = director.validate_strategy_text(bad, FAKE)
    assert any("spaceship" in item for item in problems)
    assert any("not emittable" in item for item in problems)


def test_validate_rejects_out_of_range_values():
    bad = "x = { ai_strategy = { type = air_factory_balance value = 9999 } }"
    problems = director.validate_strategy_text(bad, FAKE)
    assert any("exceeds" in item for item in problems)


def test_package_stats_counts_dropped_ids():
    stats = director.package_stats(FAKE)
    assert stats["blocks"] == len(director.PACKAGE)
    assert stats["dropped"] > 0


@pytest.mark.parametrize("call", [director.install, director.uninstall, director.status])
def test_install_requires_a_mods_directory(monkeypatch, call):
    monkeypatch.setattr(director, "install_dir", lambda: None)
    monkeypatch.setattr(director, "descriptor_path", lambda: None)
    monkeypatch.setattr(director, "strategy_path", lambda: None)
    result = call()
    if call is director.status:
        assert result["installed"] is False
    else:
        assert result["ok"] is False


def test_install_and_uninstall_roundtrip(tmp_path: Path, monkeypatch):
    mods = tmp_path / "mod"
    mods.mkdir()
    root = mods / director.MOD_FOLDER
    descriptor = mods / director.DESCRIPTOR_FILE
    target = root / "common" / "ai_strategy" / director.STRATEGY_FILE

    monkeypatch.setattr(director, "mods_dir", lambda: mods)
    monkeypatch.setattr(director, "install_dir", lambda: root)
    monkeypatch.setattr(director, "descriptor_path", lambda: descriptor)
    monkeypatch.setattr(director, "strategy_path", lambda: target)
    monkeypatch.setattr(director, "catalog", lambda force=False: FAKE)
    monkeypatch.setattr(director, "build_strategy", lambda data=None: (f"{director.MARKER}\ndummy", {"problems": [], "entries": 1, "blocks": 1, "dropped": 0}))

    installed = director.install()
    assert installed["ok"] is True
    assert target.is_file()
    assert descriptor.is_file()
    assert director.status()["installed"] is True

    removed = director.uninstall()
    assert removed["ok"] is True
    assert not target.exists()
    assert not descriptor.exists()
    assert not root.exists()


def test_uninstall_refuses_foreign_files(tmp_path: Path, monkeypatch):
    mods = tmp_path / "mod"
    root = mods / director.MOD_FOLDER
    target = root / "common" / "ai_strategy" / director.STRATEGY_FILE
    target.parent.mkdir(parents=True)
    target.write_text("someone else's mod", encoding="utf-8")
    descriptor = mods / director.DESCRIPTOR_FILE

    monkeypatch.setattr(director, "mods_dir", lambda: mods)
    monkeypatch.setattr(director, "install_dir", lambda: root)
    monkeypatch.setattr(director, "descriptor_path", lambda: descriptor)
    monkeypatch.setattr(director, "strategy_path", lambda: target)

    result = director.uninstall()
    assert result["ok"] is False
    assert target.is_file()


MARKER_LINE = director.MARKER
