"""One revision and retirement boundary for Python/CE light-tracking structure.

Reuse Project.patch_rev, the existing Patch mutation revision. Object identity
still guards project replacement; a revision catches in-place address/definition
edits. No per-frame fixture scan is added to the warm cache path.
"""
from __future__ import annotations


_CACHE_FIELDS = (
    '_lt_chan_idx_cache', '_lt_mask_cache', '_lt_scope_cache',
    '_lt_c_scope_cache', '_lt_c_state_slots', '_lt_c_color_slots',
    '_light_tracking_state', '_light_tracking_time', '_light_tracking_color_cache',
)


def structure_signature(project):
    fixtures = getattr(project, 'fixtures', ())
    definitions = getattr(project, 'fixture_defs', ())
    return (id(project), id(fixtures), len(fixtures or ()),
            id(definitions), len(definitions or ()),
            int(getattr(project, 'patch_rev', 0) or 0))


def retire_structure(engine):
    """Public structure invalidation also retires package buckets and C slots."""
    for name in _CACHE_FIELDS:
        cache = getattr(engine, name, None)
        if cache is not None:
            cache.clear()
    engine._lt_struct_memo = None
    engine._lt_pkg_map_cache = None
    engine._lt_pkg_src_to_lights = {}
    engine._lt_pkg_map_by_light_uni = {}
    engine._lt_c_state = None


def _memo_matches(previous, signature, fixtures, definitions):
    return (previous is not None and len(previous) == 6 and previous[0] == signature
            and previous[4] is fixtures and previous[5] is definitions)


def _definitions_index(definitions):
    return {str(definition.id): definition for definition in definitions or ()
            if getattr(definition, 'id', None)}


def _build_memo(signature, fixtures, definitions):
    by_universe = {}
    for fixture in fixtures or ():
        by_universe.setdefault(int(getattr(fixture, 'universe', 1) or 1), []).append(fixture)
    definitions_by_id = _definitions_index(definitions)
    fixtures_by_id = {str(getattr(fixture, 'id', '')): fixture for fixture in fixtures or ()}
    return (signature, by_universe, definitions_by_id, fixtures_by_id, fixtures, definitions)


def structure_memo(engine, project):
    """Return (six-field strong-reference memo, rebuilt) for all LT consumers."""
    fixtures = getattr(project, 'fixtures', ())
    definitions = getattr(project, 'fixture_defs', ())
    signature = structure_signature(project)
    previous = getattr(engine, '_lt_struct_memo', None)
    if _memo_matches(previous, signature, fixtures, definitions):
        return previous, False
    if previous is not None:
        retire_structure(engine)
    memo = _build_memo(signature, fixtures, definitions)
    engine._lt_struct_memo = memo
    getattr(engine, '_lt_chan_idx_cache', {}).clear()
    return memo, True
