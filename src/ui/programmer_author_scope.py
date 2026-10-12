"""Project-only author selection, separate from Programmer's programmed targets.

Reuse the original recall path for values and row state. Dispatch nodes keep
their historical target union; only project save/load carries this editor scope.
"""
from __future__ import annotations

from .programmer_joystick_state import capture_controls, restore_controls
from .programmer_project_capture import capture_project_history


def capture_editor_scope(panel, state: dict) -> dict:
    saved = capture_project_history(panel, state)
    saved["joystick_controls"] = capture_controls(panel)
    saved["editor_scope"] = {
        "version": 1,
        "fixture_ids": list(panel.get_live_selected_fixture_ids() or []),
        "group_id": str(getattr(panel, "_current_group_id", "") or ""),
        "group_name": str(getattr(panel, "_current_group_name", "") or ""),
        "base_order_ids": list(getattr(panel, "_current_base_order_ids", []) or []),
    }
    return saved


def _id_lists_valid(scope: dict) -> bool:
    for key in ("fixture_ids", "base_order_ids"):
        values = scope.get(key)
        if not isinstance(values, list):
            return False
        if any(not isinstance(value, str) for value in values):
            return False
    return True


def _saved_scope(state: dict):
    scope = state.get("editor_scope")
    if not isinstance(scope, dict):
        return None
    if type(scope.get("version")) is not int or scope["version"] != 1:
        return None
    if not _id_lists_valid(scope):
        return None
    if any(not isinstance(scope.get(key), str) for key in ("group_id", "group_name")):
        return None
    return scope


def _existing_ids(values: list, valid: set) -> list:
    ordered = []
    seen = set()
    for value in values:
        if value in valid and value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _restore_legacy(panel, state: dict) -> bool:
    # Original project recall must restore all historical T before author S.
    from .programmer_panel import _recall_node_from_state, _restore_range_states

    try:
        node = _recall_node_from_state(state)
        ok = False
        try:
            ok = bool(panel.dispatch_recall_state(node, state, for_edit=True))
        except Exception:
            ok = False
        return _restore_range_states(panel, state) or ok
    except Exception:
        return False


def _restore_author(panel, scope: dict) -> None:
    valid = {str(fixture.id) for fixture in panel._current_fixtures}
    ids = _existing_ids(scope["fixture_ids"], valid)
    base = _existing_ids(scope["base_order_ids"], valid)
    # A regular group loader merges its base into its visible selection. Load
    # the exact author IDs first, then restore the independent phase baseline.
    panel.load_fixture_group(scope["group_id"], ids, scope["group_name"], ids)
    panel._current_base_order_ids = list(base)
    if scope["group_id"] == "__topview_live__":
        panel._topview_live_base_order_ids = list(base)


def restore_editor_state(panel, state: dict | None) -> bool:
    if not state:
        return False
    scope = _saved_scope(state)
    if scope is None:
        restored = _restore_legacy(panel, state)
        restore_controls(panel, state.get("joystick_controls"))
        return restored
    previous = getattr(panel, "_suppress_topview_selection_emit", False)
    restored = False
    panel._suppress_topview_selection_emit = True
    try:
        _restore_legacy(panel, state)
        _restore_author(panel, scope)
        restore_controls(panel, state.get("joystick_controls"))
        restored = True
    except Exception:
        return False
    finally:
        panel._suppress_topview_selection_emit = previous
        if restored and not previous:
            panel._emit_fixture_selection_to_topview(panel.get_live_selected_fixture_ids())
    return True
