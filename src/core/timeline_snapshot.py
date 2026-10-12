"""Build the existing timeline undo payload without unrelated serialization.

Reuse original Track/TimelineProgram serializers and PM's scoped undo stack.
Reference: Qt v6.11.0 QUndoStack synchronous transaction boundary; Kdenlive
v25.08.0 timeline model's grouped move/finalMove contract. No C++ copied.
"""
from __future__ import annotations

from copy import copy
import time

from .models import Project, _marker_fields

_TIMELINE_FIELDS = frozenset({"tracks", "programs", "current_program_id",
                              "audio_path", "audio_clip_state", "audio_modulation_state"})
_ALLOWED_FIELDS = _TIMELINE_FIELDS | {"markers", "loop", "clip_studio_state"}


def timeline_snapshot_dict(project, domain):
    """Only the complete timeline domain (optionally markers); otherwise legacy."""
    if not isinstance(project, Project) or not domain:
        return project.to_dict(include_frames=False)
    if not _TIMELINE_FIELDS.issubset(domain) or not set(domain).issubset(_ALLOWED_FIELDS):
        return project.to_dict(include_frames=False)
    data = {
        "tracks": [] if project.programs else
                  [track.to_dict(include_frames=False) for track in project.tracks],
        "programs": _program_dicts(project),
        "current_program_id": project.current_program_id,
        "audio_path": project.audio_path,
        "audio_clip_state": dict(getattr(project, "audio_clip_state", {}) or {}),
        "audio_modulation_state": dict(getattr(project, "audio_modulation_state", {}) or {}),
    }
    data.update(_optional_fields(project, domain))
    return data


def _optional_fields(project, domain):
    data = _marker_fields(project) if domain.intersection({"markers", "loop"}) else {}
    if "clip_studio_state" in domain:
        data["clip_studio_state"] = getattr(project, "clip_studio_state", {}) or {}
    return data


def _program_dicts(project, *, include_frames=False):
    current_id = str(getattr(project, "current_program_id", "") or "")
    result = []
    for program in project.programs or []:
        if current_id and str(getattr(program, "id", "") or "") == current_id:
            result.append(_current_program_dict(program, project, include_frames=include_frames))
        else:
            result.append(program.to_dict(include_frames=include_frames))
    return result


def _current_program_dict(program, project, *, include_frames=False):
    """Keep program metadata; serialize the effective live tracks only once."""
    effective = copy(program)
    effective.tracks = project.tracks or []
    data = effective.to_dict(include_frames=include_frames)
    data["audio_path"] = getattr(project, "audio_path", "") or ""
    data["audio_clip_state"] = dict(getattr(project, "audio_clip_state", {}) or {})
    data["audio_modulation_state"] = dict(getattr(project, "audio_modulation_state", {}) or {})
    data["updated_at"] = time.time()
    return data
