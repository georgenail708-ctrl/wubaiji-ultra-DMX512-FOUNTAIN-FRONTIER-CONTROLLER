"""Hidden-slot identity mapping; all coordinate sorting is native."""
from .native_source_bridge_geometry import coordinates, ids, native


def virtual(engine, request):
    keys = ids(request["ordered_ids"])
    if not keys:
        return {}
    scope = ids(request.get("scope_ids") or keys)
    all_ids = list(dict.fromkeys(scope + keys))
    clip, proj = request.get("clip"), request.get("proj")
    actual = engine._topview_fixture_positions_for_ids(all_ids, proj=proj, clip=clip)
    if any(key not in actual for key in keys):
        return {}
    if bool(getattr(clip, "fade_group_skip_base_order", False)):
        return {key: actual[key] for key in keys if key in actual}
    if not _has_base(engine, clip, proj, actual):
        return {key: actual[key] for key in keys if key in actual}
    base = list(dict.fromkeys(key for key in scope + keys if key in actual))
    if len(base) <= 1:
        return {key: actual[key] for key in keys if key in actual}
    order = _slot_ids(engine, request, base, actual, all_ids)
    if len(order) != len(base):
        return {key: actual[key] for key in keys if key in actual}
    virtual_positions = {key: actual[order[index]] for index, key in enumerate(base)}
    return {key: virtual_positions[key] for key in keys if key in virtual_positions}


def _has_base(engine, clip, proj, actual):
    if clip is None:
        return True
    explicit = engine._unique_fixture_id_list(getattr(clip, "base_order_fixture_ids", []) or [])
    return bool(explicit or _implicit_base(engine, clip, proj, actual))


def _implicit_base(engine, clip, proj, actual):
    try:
        return [key for key in engine._clip_base_order_fixture_ids(clip, proj=proj) if key in actual]
    except Exception:
        return []


def _slot_ids(engine, request, base, actual, all_ids):
    if str(request.get("slot_mode") or "linear").lower() == "circle":
        return engine._circle_slot_order_for_virtual_positions(base, actual,
            proj=request.get("proj"), prefer_shapes=not engine._clip_has_complete_spatial_snapshot(
                request.get("clip"), all_ids))
    return [base[index] for index in native.slots(dict(xy=coordinates(base, actual), mode=1))]


def ordered(engine, request):
    keys = ids(request["ids"])
    if len(keys) <= 1:
        return keys
    direction = engine._normalize_clip_direction(request["direction"])
    if direction in ("none", ""):
        return keys
    scope = ids(request.get("scope_ids") or keys)
    all_ids = list(dict.fromkeys(scope + keys))
    clip, proj = request.get("clip"), request.get("proj")
    key = _order_key(engine, request, (keys, direction, scope, all_ids))
    cached = engine._clip_fade_geometry_cache.get(key)
    if cached is not None:
        return list(cached)
    positions = engine._direction_positions_for_fixture_ids(keys, direction, proj=proj, clip=clip,
        scope_ids=scope, start_theta=request.get("start_theta"))
    order = native.ordered(positions)
    result = tuple(keys[index] for index in order)
    engine._store_clip_fade_geometry_cache(key, result)
    return list(result)


def _order_key(engine, request, geometry):
    keys, direction, scope, all_ids = geometry
    clip, proj, start = request.get("clip"), request.get("proj"), request.get("start_theta")
    shapes = () if engine._clip_has_complete_spatial_snapshot(clip, all_ids) \
        else engine._topview_shape_signature_for_ids(all_ids, proj=proj)
    return (engine._clip_cache_id(clip) if clip is not None else "__project__",
        "direction_order", direction, tuple(keys), tuple(scope),
        bool(getattr(clip, "fade_group_skip_base_order", False)),
        engine._fixture_position_signature(all_ids, proj=proj, clip=clip), shapes,
        float(start) if start is not None else None)


def directions(engine, request):
    keys = ids(request["ids"])
    direction = engine._normalize_clip_direction(request["direction"])
    scope = ids(request.get("scope_ids") or keys)
    clip, start = request.get("clip"), request.get("start_theta")
    key = (engine._clip_cache_id(clip) if clip is not None else "__project__",
        "direction_positions_quick", direction, tuple(keys), tuple(scope),
        bool(getattr(clip, "fade_group_skip_base_order", False)),
        float(start) if start is not None else None)
    cached = engine._clip_fade_geometry_cache.get(key)
    if cached is not None:
        import numpy as np
        return np.asarray(cached, np.float32).copy()
    result = _direction_result(engine, request, (keys, direction, scope))
    engine._store_clip_fade_geometry_cache(key, tuple(float(value) for value in result))
    return result


def _direction_result(engine, request, context):
    keys, direction, scope = context
    options = dict(proj=request.get("proj"), clip=request.get("clip"), scope_ids=scope)
    for function, extra in _direction_paths(engine, direction, request.get("start_theta")):
        result = function(keys, direction, **options, **extra)
        if result is not None and len(result) == len(keys):
            return result
    if direction == "cw":
        return engine._compute_direction_positions("ltr", len(keys))
    return engine._compute_direction_positions("rtl" if direction == "ccw" else direction, len(keys))


def _direction_paths(engine, direction, start):
    if direction in ("cw", "ccw"):
        return ((engine._circle_direction_positions_for_fixture_ids, dict(start_theta=start)),
                (engine._angle_direction_positions_from_virtual_coords, {}),
                (engine._centroid_angle_direction_positions_for_fixture_ids, {}))
    return ((engine._linear_direction_positions_for_fixture_ids, {}),
            (_pattern(engine), {}))


def _pattern(engine):
    def evaluate(keys, direction, **options):
        options.pop("scope_ids", None)
        return engine._pattern_direction_positions_for_fixture_ids(keys, direction, **options)
    return evaluate
