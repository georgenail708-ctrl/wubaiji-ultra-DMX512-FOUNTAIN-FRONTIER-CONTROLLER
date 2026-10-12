"""Preserve logical-unit priority and member assembly around native metrics."""
from .native_source_bridge_geometry import coordinates, ids, native, shape_values


def inferred(engine, request):
    keys = [key for key in request["ordered_ids"] if key in request["positions"]]
    bounds, centers = native.inferred(coordinates(keys, request["positions"]))
    return [dict(cx=float(center[0]), cy=float(center[1]), items=keys[int(start):int(end)])
            for (start, end), center in zip(bounds, centers)]


def segments(engine, request):
    keys = ids(request["ordered_ids"])
    if len(keys) < 6:
        return []
    proj, clip = request.get("proj"), request.get("clip")
    if proj is None:
        from .playback_engine import _project_of
        proj = _project_of(engine)
    if proj is None:
        return []
    positions = engine._topview_fixture_positions_for_ids(keys, proj=proj, clip=clip)
    positions = {key: positions[key] for key in keys if key in positions}
    if len(positions) < 6:
        return []
    units, used = _units(engine, clip, proj, positions)
    shapes = engine._topview_circle_shapes_for_ids(keys, proj=proj)
    clusters = units + _shape_clusters(keys, positions, shapes, used)
    if len(clusters) <= 1 and not units:
        clusters = engine._infer_order_circle_segments(keys, positions)
    return _assemble(keys, clusters)


def _units(engine, clip, proj, positions):
    scope = engine._clip_spatial_scope(clip) if clip is not None else "per_shape"
    clusters, assigned = [], set()
    if scope != "per_shape":
        return clusters, assigned
    for logical in (getattr(proj, "logical_devices", None) or []):
        if not isinstance(logical, dict) or str(logical.get("kind", "")) not in ("unit", "package"):
            continue
        members = [str(value) for value in (logical.get("member_fixture_ids", []) or [])
                   if str(value) in positions]
        if len(members) < 3:
            continue
        center = native.primitive(4, coordinates(members, positions), len(members))
        clusters.append(dict(cx=float(center[0]), cy=float(center[1]), items=list(members)))
        assigned.update(members)
    return clusters, assigned


def _shape_clusters(keys, positions, shapes, used):
    if not shapes:
        return []
    existing = [key for key in keys if key in positions]
    labels = native.assignments(dict(xy=coordinates(existing, positions),
        shapes=[shape_values(shape) for shape in shapes]), [int(key in used) for key in existing])
    clusters = [dict(cx=float(shape["cx"]), cy=float(shape["cy"]), items=[], shape_index=index)
                for index, shape in enumerate(shapes)]
    assigned = set(used)
    for key, index in zip(existing, labels):
        if index >= 0 and key not in assigned:
            clusters[int(index)]["items"].append(key)
            assigned.add(key)
    return [cluster for cluster in clusters if len(cluster["items"]) >= 3]


def _normalized(keys, clusters):
    rank = {key: index for index, key in enumerate(keys)}
    result = []
    for cluster in clusters:
        members = sorted([key for key in cluster.get("items", []) if key in rank], key=rank.__getitem__)
        if len(members) >= 2:
            result.append(dict(cluster, items=members))
    result.sort(key=lambda item: min(rank[key] for key in item["items"]))
    return rank, result


def _assemble(keys, clusters):
    if len(clusters) <= 1:
        return []
    rank, clusters = _normalized(keys, clusters)
    if len(clusters) <= 1:
        return []
    by_first = {min(rank[key] for key in item["items"]): item for item in clusters}
    result, used = [], set()
    for index, key in enumerate(keys):
        cluster = by_first.get(index)
        if cluster is not None:
            members = [value for value in cluster["items"] if value not in used]
            if members:
                result.append(members)
                used.update(members)
        elif key not in used:
            result.append([key])
            used.add(key)
    result.extend([key] for key in keys if key not in used)
    return result
