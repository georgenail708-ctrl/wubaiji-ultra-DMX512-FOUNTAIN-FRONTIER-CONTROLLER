"""Patch's existing signal must invalidate the live engine as well as audio."""
from __future__ import annotations

import logging


def invalidate_patch_runtime(window):
    engine = getattr(window, '_engine', None)
    if engine is None:
        return
    try:
        # Reuse the engine's serialized generator-thread invalidation queue.
        engine.invalidate_all_caches()
    except Exception:
        logging.getLogger(__name__).exception('Could not invalidate playback after Patch edit')
