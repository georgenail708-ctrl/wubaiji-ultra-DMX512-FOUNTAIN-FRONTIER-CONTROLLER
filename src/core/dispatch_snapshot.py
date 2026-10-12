"""Exact scene transactions reuse PM history without traversing recorded shows.

Reference: Qt QUndoCommand's single editing action; existing timeline_restore.
The original Project factory owns parsing; unrelated fields keep live identity.
"""
from dataclasses import fields
import json

from .models import Project, parse_project_json

_DOMAIN = frozenset({'dispatch_state'})


def snapshot_entry(project, domain):
    if not isinstance(project, Project) or domain != _DOMAIN:
        return None
    payload = json.dumps({'dispatch_state': project.dispatch_state or {}},
                         ensure_ascii=False, separators=(',', ':'))
    return ('scoped', payload, ([], []), domain)


def restore_domain(current, before, domain):
    if not isinstance(current, Project) or domain != _DOMAIN or before is None:
        return None
    payload = json.dumps({'dispatch_state': before.get('dispatch_state') or {}},
                         ensure_ascii=False, separators=(',', ':'))
    restored = Project._from_dict(parse_project_json(payload))
    for field in fields(Project):
        if field.name not in _DOMAIN:
            setattr(restored, field.name, getattr(current, field.name))
    restored.patch_rev = current.patch_rev
    return restored
