"""Refresh Matrix targets by identity without expanding a selected subset.

R32 actual MainWindow undo evidence: r189-library-ppt.md. Reuses the existing
show identity, stage clear, fixture loading and gray MatrixSlide storage.
"""
from __future__ import annotations

from .matrix_stage_helpers import _maybe_clear_stage_on_project_switch, _pm_project
from .matrix_target_state import base_order_for_targets, target_ids_from_state


def _saved_matrix_scope(panel) -> list | None:
    """Read the persisted exact targets only for the restored current group."""
    project = _pm_project(panel)
    state = getattr(project, "matrix_slideshow_state", {}) or {}
    if str(state.get("group_id", "")) != str(getattr(panel, "_current_group_id", "")):
        return None
    ids = target_ids_from_state(state)
    if ids is None:
        return None
    engine = getattr(panel, "_engine", None)
    live = (list(engine.slides[0].states) if engine is not None and engine.slides
            else [f["id"] for f in panel._fixtures if f.get("id")])
    # The PM signal may carry an older scope. Main reload applies its frames
    # first; only that restored identity may supersede the live selection.
    return ids if set(ids) == set(live) else None


def refresh_project_fixture_scope(panel, set_mode=None) -> None:
    """Same-show refresh keeps exact targets; a new show uses normal bootstrap."""
    if _maybe_clear_stage_on_project_switch(panel, set_mode):
        panel._fixtures = []
        panel._bootstrap_from_pm()
        return
    from .matrix_running_outputs import refresh_retained
    refresh_retained(panel)
    current_ids = [f["id"] for f in panel._fixtures if f.get("id")]
    saved_ids = _saved_matrix_scope(panel) if current_ids else None
    source_ids = current_ids if saved_ids is None else saved_ids
    if not source_ids and not getattr(panel, "_current_group_id", None):
        panel._bootstrap_from_pm()
        return
    positions = panel._positions_from_pm(panel._pm)
    available = {str(f["id"]) for f in positions if f.get("id")}
    ids = base_order_for_targets(panel, [str(fid) for fid in source_ids if str(fid) in available])
    _retain_refresh_frame_targets(panel, ids)
    previous_guard = getattr(panel, "_suppress_topview_selection_emit", False)
    panel._suppress_topview_selection_emit = True
    panel._matrix_refreshing = True
    try:
        panel.load_fixture_group(panel._current_group_id, ids, panel._current_group_name,
                                 panel._pm, positions)
    finally:
        panel._matrix_refreshing = False
        panel._suppress_topview_selection_emit = previous_guard


def _retain_refresh_frame_targets(panel, ids) -> None:
    """Patch deletion trims targets by identity, preserving their gray levels."""
    engine = getattr(panel, "_engine", None)
    if engine is None or not panel._fixtures:
        return
    for slide in engine.slides:
        slide.states = {fid: slide.states.get(fid, False) for fid in ids}
