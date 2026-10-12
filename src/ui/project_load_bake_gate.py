"""Let original project/panel hydration finish before background snapshots.

Reuse the baker's existing cancellation epoch and playing-provider defer gate.
The live transport and the original project loading procedure remain independent.
"""
from functools import wraps


def bake_deferred(window, control):
    return bool(getattr(window, '_timeline_project_loading', False)
                or getattr(control, 'is_playing', False))


def invalidate(window):
    baker = getattr(window, '_timeline_baker', None)
    if baker is not None:
        baker.schedule_invalidate()


def defer_bake_during_load(original):
    @wraps(original)
    def load(window, *args, **kwargs):
        previous = getattr(window, '_timeline_project_loading', False)
        window._timeline_project_loading = True
        try:
            invalidate(window)
            return original(window, *args, **kwargs)
        finally:
            window._timeline_project_loading = previous
            invalidate(window)
    return load
