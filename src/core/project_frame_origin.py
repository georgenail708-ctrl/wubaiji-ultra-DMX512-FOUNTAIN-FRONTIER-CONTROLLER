"""Prepared project identity for the existing playback/NM mutation seams.

Reuse stdlib ContextVar and immutable serial leases; reference/limits are in
the R185 backend closeout report. No payload copies, output records or queues.
NM mutations use its existing state lock, followed by its pending lock.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

from .constants import CLIP_LAYERS


@dataclass(frozen=True, slots=True, eq=False)
class ProjectFrameLease:
    project: object
    generation: int


@dataclass(frozen=True, slots=True, eq=False)
class _FrameScope:
    engine: object
    owner: object
    project: object
    lease: object


_FRAME_SCOPE = ContextVar("qascade_project_frame_origin", default=None)


def _manager_project(engine):
    pm = getattr(engine, "_pm", None)
    return None if pm is None else (
        getattr(pm, "project", None) or getattr(pm, "_project", None))


def frame_project(engine):
    scope = _FRAME_SCOPE.get()
    if scope is not None and scope.engine is engine:
        return scope.project
    return _manager_project(engine)


def bind_project_frame(nm, project):
    """Replace the project lease while the caller holds NM's state lock."""
    lease = nm._project_frame_lease
    if lease.project is not project:
        nm._project_frame_lease = ProjectFrameLease(project, lease.generation + 1)
    nm._project_ref = project


def _capture_frame_scope(engine):
    owner = getattr(engine, "_nm", None)
    lock = getattr(owner, "_state_lock", None)
    if lock is None or not hasattr(owner, "_project_frame_lease"):
        return _FrameScope(engine, owner, _manager_project(engine), None)
    with lock:
        return _FrameScope(engine, owner, _manager_project(engine),
                           owner._project_frame_lease)


def project_frame_scope(render):
    @wraps(render)
    def scoped_render(engine, *args, **kwargs):
        token = _FRAME_SCOPE.set(_capture_frame_scope(engine))
        try:
            return render(engine, *args, **kwargs)
        finally:
            _FRAME_SCOPE.reset(token)
    return scoped_render


def project_frame_mutation_allowed(nm, layer_name):
    """Check at the actual layer mutation, with NM's state lock held."""
    scope = _FRAME_SCOPE.get()
    if scope is None or layer_name not in CLIP_LAYERS:
        return True
    current = getattr(nm, "_project_frame_lease", None)
    if current is None:
        return True  # Legacy proxy/fake has no real binding identity.
    if scope.owner is not nm:
        return False
    if scope.lease is not current:
        return False
    return current.project is None or scope.project is current.project


def _pending_entry(entry, scope):
    if scope is None or scope.lease is None:
        return entry
    if entry is None:
        return (None, None, scope.lease)
    return (entry[0], entry[1], scope.lease)


def queue_project_frame(nm, layer_name, prepared, clear_set):
    """Write the existing latest slots only after accepting prepared origin."""
    with nm._state_lock:
        if not project_frame_mutation_allowed(nm, layer_name):
            return False
        scope = _FRAME_SCOPE.get()
        if layer_name not in CLIP_LAYERS:
            scope = None
        with nm._pending_layer_updates_lock:
            updates = nm._pending_layer_updates.setdefault(str(layer_name), {})
            for universe in clear_set:
                updates[int(universe)] = _pending_entry(None, scope)
            for universe, entry in prepared.items():
                updates[int(universe)] = _pending_entry(entry, scope)
    return True


def pending_project_frame(nm, entry):
    """Decode one original pending slot under the drain's state lock."""
    if entry is None or len(entry) != 3:
        return True, entry
    data, active, lease = entry
    if lease is not nm._project_frame_lease:
        return False, None
    return True, None if data is None else (data, active)
