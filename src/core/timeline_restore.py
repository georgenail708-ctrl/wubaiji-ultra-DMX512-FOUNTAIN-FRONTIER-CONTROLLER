"""Restore a scoped timeline through the original Project factory.

Unedited domains retain their current objects and runtime patch revision. Only
the edited domain crosses the existing JSON boundary. Reuse dataclasses.fields
(CPython PSF) and original Project/PM history; no second model or history stack.
"""
from __future__ import annotations

from dataclasses import fields
import json

from .models import Project, parse_project_json
from .timeline_snapshot import _ALLOWED_FIELDS, _TIMELINE_FIELDS


def restore_timeline_domain(current, before, domain):
    """Return a fresh Qt Project for complete timeline scopes, else legacy None."""
    if not isinstance(current, Project) or not domain:
        return None
    if not _TIMELINE_FIELDS.issubset(domain) or not set(domain).issubset(_ALLOWED_FIELDS):
        return None
    data = {key: before[key] for key in domain if key in before}
    # Legacy tracks synthesize a program using these original author values.
    for name in ("name", "created_at", "bpm", "sample_rate", "timecode_fps"):
        data[name] = getattr(current, name)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    restored = Project._from_dict(parse_project_json(payload))
    for field in fields(Project):
        if field.name not in domain:
            setattr(restored, field.name, getattr(current, field.name))
    restored.patch_rev = current.patch_rev
    return restored
