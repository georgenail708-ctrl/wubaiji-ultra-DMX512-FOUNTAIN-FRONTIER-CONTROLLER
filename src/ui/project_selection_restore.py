"""Apply saved TopView UI state without broadcasting it as a user selection.

Reuses Qt QSignalBlocker and the original load_state. MainWindow already restores
each loaded panel's own saved data and refreshes its positions afterwards.
"""
from contextlib import contextmanager

from PyQt6.QtCore import QSignalBlocker


def restore_topview_state(topview, project) -> None:
    blocker = QSignalBlocker(topview)
    try:
        topview.load_state(getattr(project, "topview_state", {}) or {})
    finally:
        blocker.unblock()


def restore_programmer_state(panel, state):
    """Restore an object's scope without publishing it as a new user selection."""
    from .programmer_project_restore import restore_project_state
    previous = getattr(panel, "_suppress_topview_selection_emit", False)
    panel._suppress_topview_selection_emit = True
    try:
        return restore_project_state(panel, state)
    finally:
        panel._suppress_topview_selection_emit = previous


@contextmanager
def _passive_programmer_selection(panel):
    previous = getattr(panel, "_suppress_topview_selection_emit", False)
    panel._suppress_topview_selection_emit = True
    try:
        yield
    finally:
        panel._suppress_topview_selection_emit = previous


def load_programmer_passively(panel, state):
    """Keep ordinary Undo's original loader and manual-output semantics."""
    with _passive_programmer_selection(panel):
        return panel.load_state(state)


def refresh_programmer_geometry(panel, positions):
    """A passive position refresh does not author a new TopView selection."""
    with _passive_programmer_selection(panel):
        return panel.update_fixture_positions(positions)


def sync_effect_selection(window, ids, *, explicit=False) -> None:
    from .effect_target_scope import matches_saved_scope
    panel = getattr(window, "_effect_browser_widget", None)
    project = getattr(getattr(window, "_pm", None), "project", None)
    if not explicit and panel is not None and matches_saved_scope(panel, project):
        return
    window._sync_effect_browser_topview_selection(ids)
