"""Keep original PM history domains through Main's state restoration.

Reuse ProjectManager.entry_domain and Main's existing scoped field semantics.
References/actual RED: r189-matrix-menu-proposal.md. No new history ledger,
output owner or project-load policy; None retains the original full APIs.
"""
from __future__ import annotations

from ...core.project_manager import entry_domain

_CURVE_FIELDS = ("param_envelopes_data", "trajectory_curves_data")
_PURE_EDITOR_FIELDS = frozenset({"matrix_slideshow_state", "programmer_state", "programmer_cues_data"})


def safe_attribute(obj, name, default=None):
    """Original central getter: a failing property cannot abort other fields."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def field_requested(field: str, domain=None) -> bool:
    if domain is None:
        return True
    source_field = {"dispatch_scene_ui": "dispatch_state"}.get(field, field)
    return source_field in domain


def restore_if_requested(field, callback, *, domain=None) -> bool:
    from .undo_restore import _safe
    if not field_requested(field, domain):
        return False
    return _safe(field, callback)


def restore_project_subset(window, project, domain=None):
    from .undo_restore import restore_panels_from_project, restore_stores_from_project
    if domain is None:
        return restore_stores_from_project(project), restore_panels_from_project(window, project)
    return (restore_stores_from_project(project, domain=domain),
            restore_panels_from_project(window, project, domain=domain))


def restore_topview_selection(window, domain=None) -> None:
    pure = (isinstance(domain, frozenset) and bool(domain)
            and domain.issubset(_PURE_EDITOR_FIELDS))
    if not pure:
        topview = getattr(window, "_topview_widget", None)
        if domain is not None and "topview_state" in domain and topview is not None:
            # Restoring an authored TopView edit must update the editor scope.
            # This leaves existing playing owners bound to their captured IDs.
            ids = window._topview_explicit_selection_ids(topview)
            window._push_topview_live_selection_to_loaded_panels(ids)
            return
        window._push_topview_live_selection_to_loaded_panels()


def curve_references(project):
    return tuple(safe_attribute(project, field) for field in _CURVE_FIELDS)


def _same_curve_dictionary(field, previous, project) -> bool:
    current = safe_attribute(project, field)
    return isinstance(previous, dict) and isinstance(current, dict) and previous == current


def effective_history_domain(domain, references, project):
    if domain is None:
        return None
    unchanged = {field for field, previous in zip(_CURVE_FIELDS, references)
                 if field in domain and _same_curve_dictionary(field, previous, project)}
    return frozenset(domain) - unchanged


def timeline_cache_invalidation_requested(engine, domain) -> bool:
    return (engine is not None and hasattr(engine, "invalidate_all_caches")
            and (domain is None or not frozenset(domain).issubset(_PURE_EDITOR_FIELDS)))


def perform_history_step(window, operation: str) -> None:
    from ..main_window import _studio_flush_pending
    manager = window._pm or window._engine
    if manager and hasattr(manager, operation):
        _studio_flush_pending(window)
        stack = getattr(manager, f"_{operation}_stack", ())
        domain = entry_domain(stack[-1]) if stack else None
        previous = safe_attribute(manager, "project")
        references = curve_references(previous)
        getattr(manager, operation)()
        domain = effective_history_domain(domain, references, safe_attribute(manager, "project"))
        if domain is None:
            window._reload_panels_from_project()
        else:
            from .timeline_history_refresh import restore_timeline_edit
            from .scene_history_refresh import restore_scene_edit
            if not (restore_scene_edit(window, domain, previous)
                    or restore_timeline_edit(window, domain, previous)):
                window._reload_panels_from_project(domain=domain)
