"""Keep the four original show-pool kernels accessible outside the new catalog.

This reuses registration objects and retirement; it never creates an alias,
rewrites a show, or resurrects an unregistered preset.
"""
from .effect_retire import retire_presets


LEGACY_FOUNTAIN_IDS = frozenset({
    "fountain_height_gradient", "fountain_running", "fountain_burst", "fountain_tidal",
})


def prune_catalog_entry(engine, preset_id):
    if preset_id in LEGACY_FOUNTAIN_IDS:
        retire_presets(engine, (preset_id,))
        return
    engine._presets.pop(preset_id, None)
    if hasattr(engine, "_compute_fns"):
        engine._compute_fns.pop(preset_id, None)
