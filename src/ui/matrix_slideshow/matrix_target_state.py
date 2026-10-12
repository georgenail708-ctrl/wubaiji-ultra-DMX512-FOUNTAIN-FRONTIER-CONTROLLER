"""Persist exact Matrix targets; never infer them by expanding a mother group."""
from __future__ import annotations


def target_ids_from_state(state) -> list[str] | None:
    if not isinstance(state, dict):
        return None
    if "fixture_ids" in state:
        ids = state["fixture_ids"]
        return _unique_ids(ids) if isinstance(ids, (list, tuple)) else None
    slides = (state.get("slideshow_engine", {}) or {}).get("slides", [])
    if not slides:
        return None
    ids = [fid for slide in slides for fid in (slide.get("states", {}) or {})]
    return _unique_ids(ids)


def _unique_ids(ids) -> list[str]:
    return list(dict.fromkeys(str(fid) for fid in ids if str(fid)))


def remember_target_order(panel, ids) -> list[str]:
    """Retain the explicit input order before the existing coordinate sort."""
    ordered = _unique_ids(ids or [])
    panel._matrix_target_base_order = tuple(ordered)
    return ordered


def base_order_for_targets(panel, ids=None) -> list[str]:
    """Reorder only the current identities; a stale base can never add targets."""
    current = (_unique_ids(f["id"] for f in getattr(panel, "_fixtures", []) if f.get("id"))
               if ids is None else _unique_ids(ids))
    members = set(current)
    base = getattr(panel, "_matrix_target_base_order", ())
    return _unique_ids([fid for fid in base if fid in members] + current)


def set_coordinate_order(panel, checked) -> None:
    """The existing checkbox changes order without changing the selected scope."""
    checked = bool(checked)
    if checked == bool(getattr(panel, "_skip_base_order", False)):
        return
    panel._skip_base_order = checked
    gid = getattr(panel, "_current_group_id", None)
    if gid is None:
        return
    positions = (getattr(panel, "_all_topview_positions", None)
                 or panel._positions_from_pm(panel._pm))
    old = bool(getattr(panel, "_suppress_topview_selection_emit", False))
    panel._suppress_topview_selection_emit = True
    try:
        panel.load_fixture_group(gid, base_order_for_targets(panel),
                                 getattr(panel, "_current_group_name", ""), panel._pm, positions)
    finally:
        panel._suppress_topview_selection_emit = old


def save_group_scope(panel, state) -> None:
    state["group_name"] = getattr(panel, "_current_group_name", "")
    state["fixture_ids"] = _unique_ids(f["id"] for f in getattr(panel, "_fixtures", []) if f.get("id"))
    base = base_order_for_targets(panel, state["fixture_ids"])
    if base != state["fixture_ids"]:
        state["fixture_base_order_ids"] = base


def _restored_base_order(state, ids):
    base = state.get("fixture_base_order_ids")
    if not isinstance(base, (list, tuple)):
        return ids
    members = set(ids)
    return _unique_ids([fid for fid in _unique_ids(base) if fid in members] + ids)


def restore_group_scope(panel, state) -> None:
    gid = str(state.get("group_id", "") or "")
    name = str(state.get("group_name", "") or "")
    if gid:
        panel._current_group_id = gid
    if name:
        panel._current_group_name = name
    targets = target_ids_from_state(state)
    if targets is None:
        return
    positions = panel._positions_from_pm(panel._pm)
    available = {str(f["id"]) for f in positions if f.get("id")}
    ids = _restored_base_order(state, [fid for fid in targets if fid in available])
    panel._skip_base_order = bool(state.get("skip_base_order", False))
    old = bool(getattr(panel, "_suppress_topview_selection_emit", False))
    panel._suppress_topview_selection_emit = True
    try:
        panel.load_fixture_group(panel._current_group_id, ids, panel._current_group_name,
                                 panel._pm, positions)
    finally:
        panel._suppress_topview_selection_emit = old
