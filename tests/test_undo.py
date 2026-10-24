from core import undo


def test_push_and_undo_round_trip():
    undo.clear()
    state = {"value": 1}

    def reverse():
        state["value"] = 0
        return "reverted"

    undo.push_undo("set value", reverse)
    assert undo.can_undo()
    assert undo.peek() == "set value"
    assert undo.history() == ["set value"]
    assert undo.undo_last() == "Undone: set value. reverted"
    assert not undo.can_undo()
    assert state["value"] == 0


def test_max_depth_drops_oldest():
    undo.clear()
    for i in range(undo.MAX_DEPTH + 3):
        undo.push_undo(f"op {i}", lambda: "")
    assert len(undo.history()) == undo.MAX_DEPTH
    assert undo.history()[-1] == "op 3"


def test_undo_empty_returns_message():
    undo.clear()
    assert "nothing to undo" in undo.undo_last().lower()