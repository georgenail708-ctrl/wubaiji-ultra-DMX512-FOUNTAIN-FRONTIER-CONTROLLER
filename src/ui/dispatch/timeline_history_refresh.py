"""Refresh an exact timeline history edit without rebuilding unchanged fixtures.

Reuse R191's foreign-object identity guarantee and existing Main refresh APIs.
Other domains and factories keep the complete original reload path.
"""
from dataclasses import fields

from ...core.models import Project
from ...core.project_manager import DOMAIN_TIMELINE


def _foreign_unchanged(previous, current):
    if not isinstance(previous, Project) or not isinstance(current, Project):
        return False
    return all(getattr(previous, field.name) is getattr(current, field.name)
               for field in fields(Project) if field.name not in DOMAIN_TIMELINE)


def _move_anchor(owner, name, previous, current):
    if owner is not None and getattr(owner, name, None) is previous:
        setattr(owner, name, current)


def _rebind_editors(window, previous, current):
    nm = getattr(window, "_nm", None)
    lock = getattr(nm, "_state_lock", None)
    if lock is not None:
        from ...core.project_frame_origin import bind_project_frame
        # Both callers already proved patch/geometry object identity unchanged.
        with lock:
            bind_project_frame(nm, current)
    for attr in ("_patch_widget", "_topview_widget"):
        _move_anchor(getattr(window, attr, None), "_project", previous, current)
    for attr in ("_topview_widget", "_clip_studio_widget"):
        _move_anchor(getattr(window, attr, None), "_hydrated_for", previous, current)
    browser = getattr(window, "_effect_browser_widget", None)
    if browser is None:
        return
    _move_anchor(browser, "_group_list_project", previous, current)
    widgets = list(getattr(browser, "_param_widgets", {}).values())
    for form in getattr(browser, "_channel_forms", ()):
        widgets.extend(form.get("params", {}).values())
    for widget in widgets:
        _move_anchor(widget, "_project", previous, current)


def _refresh_dependents(window, current, previous):
    studio = getattr(window, "_clip_studio_widget", None)
    if studio is not None:
        studio.load_state(current.clip_studio_state or {}, current, session_reset=False)
    runner = getattr(window, "_route_animator_window", None)
    if runner is not None:
        runner.set_context(project_manager=window._pm, topview=window._topview_widget,
                           audio_path=current.audio_path or "")
    _restore_timeline_cache(window, previous, current)


def _restore_timeline_cache(window, previous, current):
    engine = getattr(window, "_engine", None)
    if engine is not None and hasattr(engine, "invalidate_all_caches"):
        from ...engine.timeline_source_rebind import recheck_restored_timeline
        if recheck_restored_timeline(engine, previous, current):
            gate = getattr(window, "_timeline_bake_program_gate", None)
            if (gate is not None and gate[0] is previous
                    and gate[1] == str(current.current_program_id or "") and gate[2]):
                # This exact restoration already submitted the mandatory full
                # worker check. Its program-list refresh must not overwrite it
                # with a numerical job solely because the shell was replaced.
                window._timeline_bake_program_gate = (current, gate[1], gate[2])
        else:
            engine.invalidate_all_caches()


def restore_timeline_edit(window, domain, previous) -> bool:
    """Return True only for the original exact, identity-preserving timeline case."""
    current = getattr(window._pm, "project", None)
    if domain != DOMAIN_TIMELINE or not _foreign_unchanged(previous, current):
        return False
    from ..main_window import _restore_mod_after_undo
    from ..project_save_session import note_project_edit
    window._sync_timeline_project()
    _rebind_editors(window, previous, current)
    _restore_mod_after_undo(window, current)
    _refresh_dependents(window, current, previous)
    window._refresh_program_list()
    note_project_edit(window)
    window._update_title()
    return True
