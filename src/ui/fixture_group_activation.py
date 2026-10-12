"""Shared saved-group activation; membership never expands to mother order."""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication


class _GroupSpaceOverride(QObject):
    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.ShortcutOverride
                and event.key() == Qt.Key.Key_Space
                and not event.modifiers() & (Qt.KeyboardModifier.AltModifier
                                            | Qt.KeyboardModifier.MetaModifier)):
            event.accept()
            return True
        return False


def preserve_group_space(button):
    """Let Qt activate a focused group button before Main's Space action."""
    handler = _GroupSpaceOverride(button)
    button.installEventFilter(handler)
    button._group_space_override = handler
    return button


def selected_groups(current, ordered_ids, clicked, *, control=False):
    """Programmer's plain replace / Ctrl toggle, in stable board order."""
    selected = set(current) if control else set()
    if control and clicked in selected:
        selected.remove(clicked)
    else:
        selected.add(clicked)
    return [gid for gid in ordered_ids if gid in selected]


def merge_members(group_ids, members_for_group):
    return list(dict.fromkeys(fid for gid in group_ids for fid in members_for_group(gid)))


def board_groups(panel):
    groups = getattr(panel, "_board_group_ids", None)
    if groups is None:
        return [panel._active_group_id] if panel._active_group_id else []
    if getattr(panel, "_board_selection_ids", []) != panel._ordered_selected_ids():
        return []
    return list(groups)


def activate_board(panel, gid):
    groups = list(getattr(panel._current_project(), "fixture_groups", []) or [])
    order = [str(group.get("id", "")) for group in groups]
    control = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
    selected = selected_groups(board_groups(panel), order, str(gid), control=control)
    visible = set(panel.fixture_ids()) | panel._selectable_member_ids()
    ids = [fid for fid in merge_members(selected, panel._group_fixture_ids) if fid in visible]
    # Programmer's last Ctrl-toggle restores All. TopView preserves its page
    # visibility boundary instead of inserting unplaced fixtures into the canvas.
    if not selected:
        ids = [fid for fid in panel._all_fixture_ids() if fid in visible]
    panel._active_group_id = selected[-1] if selected else ""
    panel._set_selection(ids, emit=False)
    panel._board_group_ids = selected
    panel._board_selection_ids = list(ids)
    panel._live_base_ids = list(ids)
    panel._live_selection_ids = list(ids)
    panel._live_selection_name = "TopView实时选择-设备组"
    panel.emit_selection()
    panel._sync_group_board_checked()
    panel._canvas.update()
