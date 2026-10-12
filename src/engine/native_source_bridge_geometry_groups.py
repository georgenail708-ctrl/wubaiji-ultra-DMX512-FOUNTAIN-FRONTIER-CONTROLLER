"""Keep the public group cache and member identities; native computes bounds."""
import numpy as np

from .native_source_bridge_geometry import native


def data(engine, request):
    clip, fixtures = request["clip"], request["local_fixtures"]
    direction = engine._normalize_clip_direction(request["direction"])
    context = _context(engine, request, direction)
    cached = engine._clip_fade_geometry_cache.get(context["quick"])
    if cached is not None:
        return cached
    cached = engine._clip_fade_geometry_cache.get(context["key"])
    if cached is not None:
        engine._store_clip_fade_geometry_cache(context["quick"], cached)
        return cached
    if not context["ordered"] or not fixtures:
        return _store(engine, context, _fallback(engine, len(fixtures), request["group_count"], direction))
    result = _prepare(engine, request, context)
    return _store(engine, context, result)


def _context(engine, request, direction):
    from .fade_group_overrides import overrides_signature_with_group_count
    clip, proj, track = request["clip"], request.get("proj"), request.get("track")
    fixtures = tuple(str(getattr(value, "id", "") or "") for value in request["local_fixtures"] or [])
    scope = engine._clip_spatial_scope(clip)
    ordered, _ = engine._clip_global_fixture_order_for_fade(clip, track=track, proj=proj)
    positions = engine._fixture_position_signature(ordered, proj=proj, clip=clip) if ordered else ()
    shapes = _shape_signature(engine, ordered, scope, clip, proj)
    prefix = (engine._clip_cache_id(clip), direction, int(request["group_count"] or 1), scope,
              overrides_signature_with_group_count(clip, request["group_count"]), fixtures)
    quick = (prefix[0], "seq_group_data_quick", *prefix[1:],
        str(getattr(track, "id", "") or id(track) if track is not None else ""), positions, shapes)
    key = (prefix[0], "seq_group_data", *prefix[1:], tuple(ordered), positions, shapes)
    return dict(quick=quick, key=key, ordered=ordered, scope=scope, direction=direction)


def _shape_signature(engine, ordered, scope, clip, proj):
    if ordered and scope != "global" and not engine._clip_has_complete_spatial_snapshot(clip, ordered):
        return engine._topview_shape_signature_for_ids(ordered, proj=proj)
    return ()


def _store(engine, context, arrays):
    values = tuple(np.asarray(array, np.float32) for array in arrays)
    for key in (context["key"], context["quick"]):
        copies = tuple(array.copy() for array in values)
        for array in copies:
            try:
                array.setflags(write=False)
            except Exception:
                pass
        engine._store_clip_fade_geometry_cache(key, copies)
    return values


def _fallback(engine, count, groups, direction):
    groups = max(1, int(groups or 1))
    labels = native.groups(2, count, [groups]).astype(np.float32)
    positions, sizes = np.zeros(count, np.float32), np.ones(count, np.float32)
    for group in range(groups):
        indices = np.flatnonzero(labels == group)
        if indices.size <= 0:
            continue
        sizes[indices] = float(indices.size)
        if indices.size > 1:
            values = engine._compute_direction_positions(direction, int(indices.size))
            for rank, index in enumerate(indices):
                positions[int(index)] = float(values[rank])
    return labels, positions, sizes


def _segments(engine, request, context):
    if context["scope"] == "global":
        return [context["ordered"]]
    return engine._shape_aware_order_segments_for_fade_cached(request["clip"], context["ordered"],
        proj=request.get("proj"), spatial_scope=context["scope"]) or [context["ordered"]]


def _prepare(engine, request, context):
    from .fade_group_overrides import overrides_map_for_clip
    ordered = context["ordered"]
    groups = max(1, min(int(request["group_count"] or 1), max(1, len(ordered))))
    segments = _segments(engine, request, context)
    shares = [[groups, 0]] if context["scope"] == "global" or len(segments) <= 1 \
        else native.groups(0, len(segments), [groups, len(segments)]+[
            int(any(str(key) in ordered for key in segment)) for segment in segments]).tolist()
    anchor = None
    if context["scope"] != "global" and len(segments) > 1:
        anchor = engine._direction_unified_anchor_theta(segments[0], ordered,
            proj=request.get("proj"), clip=request["clip"])
    parameters = dict(groups=groups, shares=shares, anchor=anchor,
                      overrides=overrides_map_for_clip(request["clip"], groups))
    metadata = _metadata(engine, request, context, (segments, parameters))
    return _local_arrays(engine, request, context, (metadata, groups))


def _metadata(engine, request, context, parts):
    segments, parameters = parts
    metadata = {}
    for index, segment in enumerate(segments):
        keys = [str(key) for key in segment if str(key) in context["ordered"]]
        if not keys:
            continue
        scope = context["ordered"] if context["scope"] == "global" else keys
        options = dict(proj=request.get("proj"), clip=request["clip"], scope_ids=scope)
        keys = engine._order_fixture_ids_for_direction(keys, context["direction"],
            start_theta=parameters["anchor"], **options)
        share, start_group = parameters["shares"][index]
        groups = max(1, min(share, len(keys)))
        bounds = native.groups(1, groups, [len(keys), groups, start_group])
        for start, end, group in bounds:
            members = keys[int(start):int(end)]
            if members:
                _group_members(engine, metadata, members, (int(group), parameters, options,
                    context["direction"]))
    return metadata


def _group_members(engine, metadata, keys, context):
    group, parameters, options, direction = context
    positions = engine._direction_positions_for_fixture_ids(keys,
        parameters["overrides"].get(group, direction), **options)
    for rank, key in enumerate(keys):
        metadata[str(key)] = (group, float(positions[rank]), len(keys))


def _local_arrays(engine, request, context, values):
    metadata, groups = values
    fixtures = request["local_fixtures"]
    keys = [str(getattr(fixture, "id", "")) for fixture in fixtures]
    if any(key not in metadata for key in keys):
        return _fallback(engine, len(fixtures), groups, context["direction"])
    return tuple(np.array([metadata[key][field] for key in keys], np.float32) for field in range(3))


def indices(engine, request):
    count = len(request["positions"])
    return native.groups(2, count, [max(1, int(request["group_count"] or 1))])
