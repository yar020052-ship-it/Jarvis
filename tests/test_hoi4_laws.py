from __future__ import annotations

import pytest

from actions import hoi4_laws
from actions.hoi4_live_state import _ideas_state


def variant(law_id, level, score, tags=None):
    return {
        "id": law_id,
        "level": level,
        "cost": 100,
        "score": score,
        "tags": tags or [],
        "ai_will_do": None,
        "modifiers": {},
        "group": "",
        "source": "test.txt",
    }


GROUPS = {
    "economic_cycles": {
        "id": "economic_cycles",
        "source": "test.txt",
        "origin": "mod",
        "use_list_view": True,
        "ordered": ["economic_boom", "fast_growth", "stable_growth", "recession"],
        "variants": {
            "economic_boom": variant("economic_boom", 1, 1.4),
            "fast_growth": variant("fast_growth", 2, 1.2),
            "stable_growth": variant("stable_growth", 3, 0.7),
            "recession": variant("recession", 5, -0.9),
        },
    },
    "military_spending": {
        "id": "military_spending",
        "source": "test.txt",
        "origin": "mod",
        "use_list_view": True,
        "ordered": ["defence_04", "defence_05", "defence_09"],
        "variants": {
            "defence_04": variant("defence_04", 5, 1.9),
            "defence_05": variant("defence_05", 6, 2.4),
            "defence_09": variant("defence_09", 10, 4.2),
        },
    },
    "religion": {
        "id": "religion",
        "source": "test.txt",
        "origin": "mod",
        "use_list_view": True,
        "ordered": ["christian", "pluralist", "NIG_islamic_majority_10_idea"],
        "variants": {
            "christian": variant("christian", None, 0.0),
            "pluralist": variant("pluralist", None, 0.1),
            "NIG_islamic_majority_10_idea": variant("NIG_islamic_majority_10_idea", None, 0.9),
        },
    },
}


@pytest.fixture(autouse=True)
def fake_catalog(monkeypatch):
    monkeypatch.setattr(hoi4_laws, "law_groups", lambda force=False: GROUPS)


def state(active, player="AST"):
    return {"player": player, "ideas": {"active": list(active), "count": len(active)}}


def test_current_laws_maps_active_variants_to_groups():
    assert hoi4_laws.current_laws(state(["stable_growth", "defence_04", "unrelated"])) == {
        "economic_cycles": "stable_growth",
        "military_spending": "defence_04",
    }


def test_candidates_only_adjacent_and_improving():
    candidates = hoi4_laws.law_candidates(state(["stable_growth", "defence_04", "pluralist"]))
    by_id = {item["law_id"]: item for item in candidates}
    assert "fast_growth" in by_id
    assert by_id["fast_growth"]["delta"] > 0.15
    # Two levels away: never requested automatically.
    assert "economic_boom" not in by_id
    assert "defence_05" in by_id
    assert "defence_09" not in by_id
    # No numeric tier on either side: not a grounded "one step" change.
    assert "NIG_islamic_majority_10_idea" not in by_id


def test_candidates_reject_country_specific_variants():
    groups = dict(GROUPS)
    groups["economic_cycles"] = {
        **GROUPS["economic_cycles"],
        "ordered": ["stable_growth", "NIG_fake_next"],
        "variants": {
            **GROUPS["economic_cycles"]["variants"],
            "NIG_fake_next": variant("NIG_fake_next", 4, 2.0),
        },
    }
    hoi4_laws.law_groups = lambda force=False: groups  # type: ignore[assignment]
    try:
        candidates = hoi4_laws.law_candidates(state(["stable_growth"]))
        assert all(item["law_id"] != "NIG_fake_next" for item in candidates)
    finally:
        hoi4_laws.law_groups = lambda force=False: GROUPS  # type: ignore[assignment]


def test_best_law_task_is_limited_to_auto_groups():
    # military_spending has a bigger delta, but is not an auto group.
    assert hoi4_laws.best_law_task(
        state(["stable_growth", "defence_04"])
    ) == {
        "kind": "change_law",
        "law_id": "fast_growth",
        "law_group": "economic_cycles",
        "reason": "stable_growth -> fast_growth adds +0.50 weighted modifier score",
    }


def test_prepare_accepts_one_step_change():
    actions, status = hoi4_laws.prepare_law_task(
        {"kind": "change_law", "law_id": "fast_growth"},
        state(["stable_growth"]),
    )
    assert status.startswith("validated")
    assert actions == [{
        "do": "law_click",
        "law_id": "fast_growth",
        "law_group": "economic_cycles",
        "law_current": "stable_growth",
    }]


def test_prepare_rejects_non_adjacent_already_active_and_unknown():
    actions, status = hoi4_laws.prepare_law_task(
        {"kind": "change_law", "law_id": "economic_boom"}, state(["stable_growth"])
    )
    assert actions == [] and "not adjacent" in status

    actions, status = hoi4_laws.prepare_law_task(
        {"kind": "change_law", "law_id": "stable_growth"}, state(["stable_growth"])
    )
    assert actions == [] and "already in effect" in status

    actions, status = hoi4_laws.prepare_law_task(
        {"kind": "change_law", "law_id": "spaceship_law"}, state(["stable_growth"])
    )
    assert actions == [] and "unknown law id" in status


def test_prepare_rejects_missing_current_anchor_and_pending_duplicate():
    actions, status = hoi4_laws.prepare_law_task(
        {"kind": "change_law", "law_id": "fast_growth"}, state([])
    )
    assert actions == [] and "cannot identify the current" in status

    actions, status = hoi4_laws.prepare_law_task(
        {"kind": "change_law", "law_id": "fast_growth"},
        state(["stable_growth"]),
        [{"kind": "change_law", "law_group": "economic_cycles"}],
    )
    assert actions == [] and "awaiting save confirmation" in status


def test_verify_law_pending_states():
    pending = {
        "law_id": "fast_growth",
        "baseline": {"path": "a", "modified": 1, "size": 2},
    }
    assert hoi4_laws.verify_law_pending(
        pending, {"ideas": {"active": ["fast_growth"]}}
    ) == "completed"
    assert hoi4_laws.verify_law_pending(
        pending,
        {"path": "a", "modified": 1, "size": 2, "ideas": {"active": ["stable_growth"]}},
    ) == "pending"
    assert hoi4_laws.verify_law_pending(
        pending,
        {"path": "a", "modified": 3, "size": 4, "ideas": {"active": ["stable_growth"]}},
    ) == "failed"


def test_ideas_state_parses_flat_save_block():
    country = """
{
\tideas={ Major_Non_NATO_Ally at_member "quoted_idea" corruption_level_02 }
\ttechnology={ technologies={ infantry_weapons2 } }
}
"""
    parsed = _ideas_state(country)
    assert parsed["count"] == 4
    assert parsed["active"] == [
        "Major_Non_NATO_Ally",
        "at_member",
        "quoted_idea",
        "corruption_level_02",
    ]


def test_infer_level_uses_trailing_tier():
    assert hoi4_laws._infer_level("corruption_level_10", None) == 10
    assert hoi4_laws._infer_level("defence_04", None) == 4
    assert hoi4_laws._infer_level("pluralist", None) is None
    assert hoi4_laws._infer_level("corruption_level_10", 3) == 3


GUI_TEXT = """
guiTypes = {
\tcontainerWindowType = {
\t\tname = "countrypoliticsview"
\t\tposition = { x=-606 y=78 }
\t\tshow_position = { x=-6 y=78 }
\t\tcontainerWindowType = {
\t\t\tname = "ideas"
\t\t\tposition = { x=9 y=438 }
\t\t\tgridBoxType = {
\t\t\t\tname = "idea_categories_grid"
\t\t\t\tposition = { x=9 y=0 }
\t\t\t\tsize = { width=100% height=100% }
\t\t\t\tslotsize = { width=480 height=115 }
\t\t\t}
\t\t}
\t}
\tcontainerWindowType = {
\t\tname = "political_ideas_window"
\t\tposition = { x=-356 y=450 }
\t\tshow_position = { x=544 y=80 }
\t\tsize = { width=500 height=590 }
\t}
}
"""


def test_law_gui_absolute_geometry_uses_show_position():
    from actions import hoi4_law_gui as gui

    nodes = gui._nodes(GUI_TEXT)
    categories = gui._absolute(nodes, "idea_categories_grid", "gridboxtype")
    assert categories is not None
    assert (round(categories["x"]), round(categories["y"])) == (12, 516)
    assert categories["slot"] == (480.0, 115.0)

    window = gui._absolute(nodes, "political_ideas_window")
    assert window is not None
    assert (round(window["x"]), round(window["y"])) == (544, 80)
    assert window["size"] == (500.0, 590.0)
