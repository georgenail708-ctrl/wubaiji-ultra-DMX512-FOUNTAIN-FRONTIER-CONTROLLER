"""R33 frame edits share the existing scoped Matrix history.

Reuse matrix_ppt_catalog snapshot/store and the original slide engine/Qt thumbs.
References and RED evidence: ai-memory/_references/r189-matrix-menu-proposal.md.
Dialogs stay in the original slot; cancellation never enters these commits.
"""
from .matrix_ppt_catalog import snapshot_sequence_edit, store_sequence_edit
from .matrix_slide_engine import MatrixSlide


def _mark_dirty(panel):
    from .matrix_slideshow_panel import _mark_panel_dirty
    _mark_panel_dirty(panel)


def _finish_sequence_edit(panel, idx):
    _mark_dirty(panel)
    panel._rebuild_thumbs()
    panel._on_thumb_clicked(idx)
    store_sequence_edit(panel)


def add_blank_frame(panel):
    slide = MatrixSlide(
        name=f"帧 {len(panel._engine.slides) + 1}",
        states={f.get("id", ""): False for f in panel._fixtures},
    )
    snapshot_sequence_edit(panel)
    idx = panel._engine.add_slide(slide)
    _finish_sequence_edit(panel, idx)


def copy_frame(panel):
    idx = panel._engine.current_idx
    if not 0 <= idx < len(panel._engine.slides):
        return
    snapshot_sequence_edit(panel)
    new_idx = panel._engine.duplicate_slide(idx)
    _finish_sequence_edit(panel, new_idx)


def delete_frame(panel):
    idx = panel._engine.current_idx
    count = len(panel._engine.slides)
    if not 0 <= idx < count or count <= 1:
        return
    snapshot_sequence_edit(panel)
    panel._engine.remove_slide(idx)
    _finish_sequence_edit(panel, min(idx, count - 2))


def move_frame(panel, direction):
    idx = panel._engine.current_idx
    count = len(panel._engine.slides)
    new_idx = idx + direction
    if not 0 <= idx < count or not 0 <= new_idx < count or new_idx == idx:
        return
    snapshot_sequence_edit(panel)
    panel._engine.move_slide(idx, direction)
    _finish_sequence_edit(panel, new_idx)


def rename_frame(panel, name):
    idx = panel._engine.current_idx
    if not 0 <= idx < len(panel._engine.slides):
        return
    slide = panel._engine.slides[idx]
    if name == slide.name:
        return
    snapshot_sequence_edit(panel)
    slide.name = name
    _mark_dirty(panel)
    if idx < len(panel._thumbs):
        panel._thumbs[idx].update_slide(slide)
        panel._thumbs[idx].update()
    store_sequence_edit(panel)
