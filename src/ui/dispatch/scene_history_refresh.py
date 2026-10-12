"""Restore an exact scene transaction without reloading unchanged device editors.

Reuse PM's foreign identity contract, R205 editor anchors and R183 scene restore.
The running scene ledger remains live; ordinary project reload is the fallback.
"""
from dataclasses import fields

from ...core.models import Project
from ...core.project_manager import DOMAIN_DISPATCH
from .timeline_history_refresh import _rebind_editors


def restore_scene_edit(window, domain, previous):
    current = getattr(window._pm, 'project', None)
    if domain != DOMAIN_DISPATCH or not isinstance(previous, Project) or not isinstance(current, Project):
        return False
    if not all(getattr(previous, field.name) is getattr(current, field.name)
               for field in fields(Project) if field.name not in DOMAIN_DISPATCH):
        return False
    from .undo_restore import restore_stores_from_project
    from ..project_save_session import note_project_edit
    window._sync_timeline_project()
    _rebind_editors(window, previous, current)
    restore_stores_from_project(current, domain=domain)
    note_project_edit(window)
    window._update_title()
    return True
