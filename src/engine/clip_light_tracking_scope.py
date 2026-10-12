"""Clip-owned light-tracking targets, shared by Python and CE compilation.

Reuse the recorder's explicit zero ownership and existing frame/domain indexes.
SceneValue writes only authored channels; searching another source never
adds output targets. Package pairing remains PlaybackEngine's existing mapping.
"""
from __future__ import annotations

import numpy as np

from ..core.recorded_channel_masks import recorded_mask_at
from ..utils.identity_cache import bounded_put
from .light_tracking_structure import structure_signature

_RGBW = frozenset(("r", "red", "g", "green", "b", "blue", "w", "white"))
_EMPTY = np.zeros(512, dtype=bool)
_EMPTY.setflags(write=False)


def _clip_map(clip, name):
    return getattr(clip, name, None) or {}


def _explicit_mask(channels):
    mask = np.zeros(512, dtype=bool)
    for channel in channels or ():
        index = int(channel) - 1
        if 0 <= index < 512:
            mask[index] = True
    return mask


def _owned_mask(engine, clip, universe):
    """Union ownership, without base_order or the legacy all-512 fallback."""
    explicit = _clip_map(clip, "active_channels_by_universe")
    key = universe if universe in explicit else str(universe)
    if key in explicit:
        return _explicit_mask(explicit[key])  # Empty is explicit; zero-valued channels still own.
    samples = _clip_map(clip, "recorded_channel_masks")
    events = samples.get(universe, samples.get(str(universe), ()))
    mask = np.zeros(512, dtype=bool)
    if events:
        for stamp, _packed in events:
            mask |= recorded_mask_at(clip, universe, stamp)
        return mask
    # For u8 frames, first-nonzero OR later-changed has the same union as any-nonzero.
    # Reuse the indexed frames; never scan every clip frame again for each universe.
    for frame in engine._clip_frame_index_for_universe(clip, universe):
        mask |= engine._coerce_dmx_data(frame.data) > 0
    return mask


def _owned_masks(engine, clip):
    domains = engine._clip_frame_universes(clip)
    for name in ("active_channels_by_universe", "recorded_channel_masks"):
        domains.update(int(universe) for universe in _clip_map(clip, name))
    return {universe: _owned_mask(engine, clip, universe) for universe in domains}


def _fixture_channels(fixture, definitions, source):
    definition = definitions.get(str(getattr(fixture, "def_id", "")))
    channels = getattr(definition, "channels", None) or ()
    start = int(getattr(fixture, "start_channel", 1) or 1) - 1
    occupied, sources, lights = [], [], []
    for offset, channel in enumerate(channels):
        index = start + offset
        if not 0 <= index < 512:
            continue
        occupied.append(index)
        kind = str(getattr(channel, "value", channel)).strip().lower()
        if kind == source:
            sources.append(index)
        elif kind in _RGBW:
            lights.append(index)
    return int(getattr(fixture, "universe", 1) or 1), occupied, sources, lights


def _owned_fixtures(records, owned):
    members, sources = set(), set()
    for identity, (universe, channels, source_channels, _lights) in records.items():
        mask = owned.get(universe, _EMPTY)
        if mask[channels].any():
            members.add(identity)
        if mask[source_channels].any():
            sources.add(identity)
    return members, sources


def _mark_lights(masks, records, identities):
    for identity in identities:
        record = records.get(identity)
        if record is None:
            continue
        universe, _channels, _source, lights = record
        if lights:
            masks.setdefault(universe, np.zeros(512, dtype=bool))[lights] = True


def _build_masks(engine, clip, project, source):
    fixtures = getattr(project, "fixtures", None) or ()
    definitions = engine._fixture_defs_index(project)
    records = {str(fixture.id): _fixture_channels(fixture, definitions, source)
               for fixture in fixtures}
    members, owned_sources = _owned_fixtures(records, _owned_masks(engine, clip))
    masks = {}
    integrated = {identity for identity in members if records[identity][2]}
    _mark_lights(masks, records, integrated)
    packages = engine._lt_ensure_pkg_map(project, definitions, source)
    for source_id, lights in packages.items():
        # A selected light never promotes its unowned siblings into targets.
        selected = lights if source_id in owned_sources else members.intersection(lights)
        _mark_lights(masks, records, selected)
    for mask in masks.values():
        mask.setflags(write=False)
    return masks


def _scope_cache_entry(engine, clip, project, source):
    """Identity-checked cache read for one clip revision: (key, refs, hit|None)."""
    fixtures = getattr(project, "fixtures", None) or ()
    definitions = getattr(project, "fixture_defs", None) or ()
    frames = getattr(clip, "frames", None) or ()
    refs = (project, clip, fixtures, definitions, frames, engine._clip_static_signature(clip),
            getattr(clip, "active_channels_by_universe", None),
            getattr(clip, "recorded_channel_masks", None))
    key = (engine._clip_cache_id(clip), source, structure_signature(project), tuple(id(ref) for ref in refs),
           (len(fixtures), len(definitions), len(frames)))
    cached = engine._lt_mask_cache.get(key)
    if cached is not None and all(left is right for left, right in zip(cached[2], refs)):
        return key, refs, cached
    return key, refs, None


def light_tracking_target_mask(engine, clip, universe, project):
    """Build all domains once per clip revision; warm reads remain constant-time."""
    if project is None or not getattr(clip, "light_tracking_enabled", False):
        return _EMPTY
    source = str(getattr(clip, "light_tracking_source", "pump") or "pump").strip().lower()
    if source not in {"pump", "valve"}:
        source = "pump"
    key, refs, cached = _scope_cache_entry(engine, clip, project, source)
    if cached is not None:
        return cached[0].get(int(universe), _EMPTY)
    masks = _build_masks(engine, clip, project, source)
    # Keep strong refs as well as ids: replacement objects cannot reuse an old cache key.
    bounded_put(engine._lt_mask_cache, key, (masks, project, refs, clip), 1024)
    return masks.get(int(universe), _EMPTY)


def target_package_lights(identities, fixtures, definitions, universe, target_mask):
    """Filter before touching the shared pump envelope or light color state."""
    result = []
    for identity in identities:
        fixture = fixtures.get(identity)
        if fixture is None:
            continue
        fixture_universe, _channels, _source, lights = _fixture_channels(fixture, definitions, "")
        if fixture_universe == int(universe) and target_mask[lights].any():
            result.append(identity)
    return result


def owned_rgbw_indices(rgbw_indices, target_mask):
    """Restrict one fixture's RGB/W channels to owned targets; None = nothing owned."""
    filtered = {name: [index for index in indices if target_mask[index]]
                for name, indices in rgbw_indices.items()}
    return filtered if any(filtered.values()) else None


def source_visit_exhausted(visits, source_id, universe, light_count):
    """Count one visit of this cross-track source; True when no owned light remains."""
    key = (source_id, universe)
    visit = visits.get(key, 0)
    visits[key] = visit + 1
    return visit >= light_count


def lt_intensity_params(clip, default_attack, default_release):
    """(source, threshold, attack, release, max_brightness) with the clip's defaults."""
    source = str(getattr(clip, "light_tracking_source", "pump") or "pump").strip().lower()
    if source not in {"pump", "valve"}:
        source = "pump"
    threshold = max(0, min(255, int(getattr(clip, "light_tracking_threshold", 128) or 128)))
    attack = max(0.01, float(getattr(clip, "light_tracking_attack", default_attack) or default_attack))
    release = max(0.01, float(getattr(clip, "light_tracking_release", default_release) or default_release))
    # 0 是合法输入(对话框 0-255, 用户输 0 意图灯保持灭), 不能用
    # `or 255` — 否则 0 被反转成 255 全亮(EDGE-03)。
    raw_max = getattr(clip, "light_tracking_max_brightness", None)
    max_brightness = max(0, min(255, int(255 if raw_max in (None, "") else raw_max)))
    return source, threshold, attack, release, max_brightness


def lt_color_params(clip):
    """(cr, cg, cb, color_is_white); white means "inherit the fixture's live color"."""
    color = str(getattr(clip, "light_tracking_color", "#FFFFFF") or "#FFFFFF").strip().upper()
    stripped = color.lstrip("#")
    color_is_white = stripped in ("FFFFFF", "ffffff", "") or len(stripped) != 6
    digits = color[1:] if color.startswith("#") else color
    if len(digits) != 6:
        digits = "FFFFFF"
    try:
        return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), color_is_white
    except Exception:
        return 255, 255, 255, color_is_white
