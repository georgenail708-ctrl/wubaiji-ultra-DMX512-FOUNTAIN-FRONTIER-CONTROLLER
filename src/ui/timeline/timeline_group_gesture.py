"""R24 transient group gesture on original ClipItem/Timeline/PM interfaces.

Reference: existing R141 scoped snapshot/batch refresh; Kdenlive v25.08.0 group
operation contract, no GPL code copied. No editor stack, timer, frames or output.
Frozen source/RED/compatibility contract: ai-memory/_references/r189-library-pixel.md.
"""
from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QToolTip

from . import timeline_group_transform as plans


def _root(widget):
    parent = widget
    for _step in range(8):
        if parent is None:
            break
        if hasattr(parent, "ctrl") and hasattr(parent, "_selected_clip_ids"):
            return parent
        parent = parent.parent()
    return None


def select_item(view, clip_id, shift, ctrl):
    root = _root(view)
    selected = set(root._selected_clip_ids()) if root is not None else view._selected_ids
    preserve = not (shift or ctrl) and clip_id in selected and len(selected) > 1
    view._group_preserve_selection = preserve
    if ctrl:
        view._selected_ids ^= {clip_id}
    elif shift and view._selected_ids:
        _range_select(view, clip_id)
    elif not preserve:
        view._selected_ids = {clip_id}
    for cid, item in view._items.items():
        item.set_selected(cid in view._selected_ids)
    try:
        view.clip_selected.emit(clip_id, shift, ctrl)
    finally:
        view._group_preserve_selection = False


def _range_select(view, clip_id):
    ids = [clip.id for clip in sorted(view._track.clips, key=lambda clip: clip.start_time)]
    if not ids:
        return
    anchor = next(iter(view._selected_ids))
    a = ids.index(anchor) if anchor in ids else 0
    b = ids.index(clip_id) if clip_id in ids else 0
    view._selected_ids = set(ids[min(a, b): max(a, b) + 1])


class _EscapeFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        if event.key() != Qt.Key.Key_Escape or not _owns_escape(self.parent()):
            return False
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()  # Original Main Stop must wait for the active edit gesture.
            return True
        if cancel(self.parent()):
            event.accept()
            return True
        return False


def _owns_escape(item):
    state = getattr(item, "_group_gesture", None)
    if state is None or state["committed"]:
        return False
    root = state["root"]
    return (item._is_dragging and state["project"] is root._session
            and state is getattr(root, "_group_gesture", None))


def prepare(item, resize):
    root = _root(item)
    ids = root._selected_clip_ids() if root is not None else []
    rows = plans.members(root._session, ids) if item._clip.id in ids else None
    if rows is None or len(rows) < 2 or item._drag_mode not in ("move", "resize"):
        return item._snapshot_multi_orig_durs() if resize else None
    pm = root._get_pm()
    state = dict(item=item, root=root, project=root._session, rows=rows, pm=pm,
                 undo=list(pm._undo_stack) if pm is not None else [],
                 redo=list(pm._redo_stack) if pm is not None else [], marker=None,
                 mode=item._drag_mode, committed=False)
    root._group_gesture = state
    item._group_gesture = state
    state["filter"] = _EscapeFilter(item)
    QApplication.instance().installEventFilter(state["filter"])
    return {row.clip.id: row.duration for row in rows} if resize else None


def snapshot(item, root):
    root._snapshot_for_undo_scoped()
    state = getattr(item, "_group_gesture", None)
    if state is not None and state["pm"] is not None:
        stack = state["pm"]._undo_stack
        state["marker"] = stack[-1][2] if stack else None


def _active(root, clip_id):
    state = getattr(root, "_group_gesture", None)
    if state is None or state["item"]._clip.id != clip_id:
        return None
    return state


def _target(root, global_y):
    y = root._clip_area.global_y_to_content_y(global_y)
    return next((tid for tid, (top, bottom) in root._clip_area.get_track_y_map().items()
                 if top <= y < bottom), None)


def _geometry(root, ids):
    for view in root._iter_clip_views():
        for cid, item in view._items.items():
            if cid in ids:
                item._update_geometry()
                item.update()


def preview_move(root, clip_id, global_y, start):
    state = _active(root, clip_id)
    if state is None:
        root.ctrl.handle_cross_track_drag_preview(clip_id,
            root._clip_area.global_y_to_content_y(global_y), start, root._clip_area.get_track_y_map())
        return
    if state["project"] is not root._session:
        cancel(state["item"])
        return
    plan = plans.move_plan(state["rows"], plans.clip_tracks(root._session), clip_id, start)
    if plan is None:
        return
    for clip, _track, position in plan:
        clip.start_time = position
    item = state["item"]
    item._drag_last_start = item._clip.start_time
    _preview_refresh(root, [clip.id for clip, _track, _pos in plan])


def commit_move(root, clip_id, global_y, start):
    state = _active(root, clip_id)
    if state is None:
        pre = root._consume_pre_snap(clip_id)
        root.ctrl.handle_cross_track_drag(clip_id, root._clip_area.global_y_to_content_y(global_y),
            start, root._clip_area.get_track_y_map(), snapshot=not pre)
        return
    target = _target(root, global_y)
    if state["project"] is not root._session or target is None:
        cancel(state["item"])
        return
    plan = plans.move_plan(state["rows"], plans.clip_tracks(root._session), clip_id, start,
                           track_id=target)
    if plan is None:
        cancel(state["item"])
        return
    _ensure_snapshot(state)
    _apply_move(root, plan)
    state["committed"] = True


def _ensure_snapshot(state):
    if state["marker"] is None:
        snapshot(state["item"], state["root"])
    state["item"]._pre_snapped = False


def _apply_move(root, plan):
    moved_tracks = False
    for clip, target, position in plan:
        current, _same = root.ctrl._find_track_and_clip(clip.id)
        clip.start_time = position
        if current is not target:
            current.clips = [member for member in current.clips if member is not clip]
            target.clips.append(clip)
            clip.track_id = target.id
            moved_tracks = True
    if moved_tracks:
        for track in plans.clip_tracks(root._session):
            track.clips.sort(key=lambda clip: float(clip.start_time))
        root.ctrl.tracks_changed.emit()
        _select_group(root, {clip.id for clip, _track, _pos in plan})
    root._refresh_clips_targeted([clip.id for clip, _track, _pos in plan])
    root._mark_project_dirty()


def preview_resize(item, requested):
    state = getattr(item, "_group_gesture", None)
    if state is None:
        _legacy_resize(item, requested)
        return
    if state["project"] is not state["root"]._session:
        cancel(item)
        return
    plan = plans.resize_plan(state["rows"], item._clip.id, requested)
    if plan is None:
        cancel(item)
        return
    for clip, duration in plan:
        clip.duration = duration
    _preview_refresh(state["root"], [clip.id for clip, _duration in plan])


def commit_resize(root, clip_id, duration):
    state = _active(root, clip_id)
    if state is None:
        _legacy_resize_commit(root, clip_id)
        return
    if state["project"] is not root._session:
        cancel(state["item"])
        return
    _ensure_snapshot(state)
    for row in state["rows"]:
        root.ctrl.resize_clip(row.clip.id, row.clip.duration, snapshot=False)
    root._refresh_clips_targeted([row.clip.id for row in state["rows"]])
    root._mark_project_dirty()
    state["committed"] = True


def _legacy_resize(item, requested):
    root = getattr(item, "_root_ref", None) or item._find_timeline_root()
    item._root_ref = root
    if root is None:
        return
    ids = _other_selected(root, item._clip.id)
    delta = requested - item._drag_start_val
    if not ids or abs(delta) < 1e-9:
        return
    original = getattr(item, "_multi_orig_durs", None) or {}
    _resize_selected(root, ids, original, delta)


def _other_selected(root, clip_id):
    try:
        return set(root._selected_clip_ids() or []) - {clip_id}
    except Exception:
        return set()


def _resize_selected(root, ids, original, delta):
    for view in root._iter_clip_views():
        for other in view._items.values():
            clip = other._clip
            if clip.id in ids:
                _resize_other(other, original, delta)


def _select_group(root, ids):
    for view in root._iter_clip_views():
        view.set_selected_ids({clip.id for clip in view.track.clips if clip.id in ids})


def _preview_refresh(root, ids):
    root._refresh_clips_targeted(ids)
    _geometry(root, set(ids))


def _resize_other(item, original, delta):
    clip = item._clip
    base = original.get(clip.id, max(.1, float(getattr(clip, "duration", .1) or .1)))
    clip.duration = max(.1, base + delta)
    item._update_geometry()


def _legacy_resize_commit(root, clip_id):
    ids = root._resolve_context_clip_ids(clip_id)
    if clip_id not in ids:
        ids = [clip_id]
    if not root._consume_pre_snap(clip_id):
        root._snapshot_for_undo()
    changed = []
    for cid in ids:
        _track, clip = root.ctrl._find_track_and_clip(cid)
        if clip is not None:
            root.ctrl.resize_clip(cid, max(.05, float(clip.duration)), snapshot=False)
            changed.append(cid)
    if changed:
        root._refresh_clips_targeted(changed)


def _restore_history(state):
    pm, marker = state["pm"], state["marker"]
    if pm is None or marker is None or not pm._undo_stack:
        return
    if pm._undo_stack[-1][2] is marker:
        pm._undo_stack[:] = state["undo"]
        pm._redo_stack[:] = state["redo"]


def cancel(item):
    state = getattr(item, "_group_gesture", None)
    if state is None or state["committed"]:
        return False
    if state["project"] is state["root"]._session:
        for row in state["rows"]:
            row.clip.start_time, row.clip.duration = row.start, row.duration
        _restore_history(state)
        _preview_refresh(state["root"], [row.clip.id for row in state["rows"]])
    _detach(state)
    item.releaseMouse()
    item._is_dragging, item._drag_moved, item._pre_snapped = False, False, False
    item._drag_mode, item._multi_orig_durs = None, None
    item._drag_local_x, item._drag_last_start = None, None
    state["root"]._clip_area.set_snap_guide(None)
    QToolTip.hideText()
    from .timeline_drag_tip import hide
    hide(item)
    item.update()
    return True


def _detach(state):
    QApplication.instance().removeEventFilter(state["filter"])
    state["filter"].deleteLater()
    state["root"]._group_gesture = None
    state["item"]._group_gesture = None


def release(item):
    state = getattr(item, "_group_gesture", None)
    if state is not None:
        if not state["committed"]:
            cancel(item)
            return
        else:
            _detach(state)
    item.releaseMouse()
