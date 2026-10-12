"""Keep a restored Matrix scope distinct from an explicit TopView edit.

R19/R32 actual MainWindow evidence: native10004 and the existing scoped-undo
restore contract. This policy reads identities only; the original loader still
owns selection updates, stage remapping and feedback suppression.
"""
from __future__ import annotations

from .matrix_slideshow.matrix_target_state import target_ids_from_state


def preserve_restored_matrix_scope(window, panel) -> bool:
    if panel is None:
        return False
    project = getattr(getattr(window, "_pm", None), "project", None)
    state = getattr(project, "matrix_slideshow_state", {}) or {}
    if not isinstance(state, dict):
        return False
    if str(state.get("group_id", "")) != str(getattr(panel, "_current_group_id", "")):
        return False
    saved_ids = target_ids_from_state(state)
    if saved_ids is None:
        return False
    current_ids = [str(f["id"]) for f in getattr(panel, "_fixtures", []) if f.get("id")]
    return current_ids == [str(fid) for fid in saved_ids]
