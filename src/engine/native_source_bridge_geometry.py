"""Source geometry metadata: IDs, scopes and snapshots stay in public order."""
from qascade_ce import native_source_motion_geometry as native


def ids(values):
    return [str(value) for value in (values or []) if str(value)]


def coordinates(keys, positions):
    return [(float(positions[key][0]), float(positions[key][1])) for key in keys]


def shape_values(shape):
    return [float(shape[key]) for key in ("cx", "cy", "rx", "ry")] + [
        float(shape.get("angle", 0.) or 0.)]


def spatial(engine, request, virtual=False):
    keys = ids(request["ids"])
    scope = ids(request.get("scope_ids") or keys)
    all_ids = list(dict.fromkeys(scope + keys))
    options = dict(proj=request.get("proj"), clip=request.get("clip"))
    if virtual:
        positions = engine._virtual_topview_fixture_positions_for_ids(all_ids,
            scope_ids=scope, slot_mode="linear", **options)
    else:
        positions = engine._topview_fixture_positions_for_ids(all_ids, **options)
    actual_scope = [key for key in scope if key in positions] or keys
    return keys, scope, all_ids, positions, actual_scope


def fit(engine, request):
    keys = [str(key) for key in (request["ids"] or []) if str(key) in request["positions"]]
    shapes = engine._topview_circle_shapes_for_ids(keys, proj=request.get("proj")) \
        if len(keys) >= 3 and request.get("prefer_shapes", True) else []
    flags = int(request.get("prefer_shapes", True)) | (2 * int(request.get("allow_bbox_fallback", True)))
    result = native.fit(dict(xy=coordinates(keys, request["positions"]),
        shapes=[shape_values(shape) for shape in shapes], flags=flags))
    if not result[0]:
        return None
    if result[1] >= 0:
        return dict(shapes[int(result[1])])
    return dict(type="ellipse" if result[7] else "circle", cx=float(result[2]),
                cy=float(result[3]), rx=float(result[4]), ry=float(result[5]), angle=float(result[6]))


def metric(engine, request):
    result = native.metrics(shape_values(request["shape"]), [request["pos"]])[0]
    return float(result[0]), float(result[1])


def linear(engine, request):
    direction = engine._normalize_clip_direction(request["direction"])
    if direction not in ("ltr", "rtl", "ftb", "btf", "ctr", "rtc"):
        return None
    if len(ids(request["ids"])) <= 1:
        return trivial(request)
    keys, _, _, positions, scope = spatial(engine, request, virtual=True)
    if any(key not in positions for key in keys):
        return None
    return native.linear(direction, coordinates(keys, positions), coordinates(scope, positions))


def angular(engine, request):
    direction = engine._normalize_clip_direction(request["direction"])
    if direction not in ("cw", "ccw"):
        return None
    if len(ids(request["ids"])) <= 1:
        return trivial(request)
    keys, _, _, positions, scope = spatial(engine, request)
    if any(key not in positions for key in keys):
        return None
    mode = 2 if request["name"].startswith("_centroid") else 1
    return native.ranks(dict(xy=coordinates(keys, positions), scope=coordinates(scope, positions),
                             clockwise=int(direction == "cw"), mode=mode))


def circle(engine, request):
    direction = engine._normalize_clip_direction(request["direction"])
    if direction not in ("cw", "ccw"):
        return None
    if len(ids(request["ids"])) <= 1:
        return trivial(request)
    keys, scope_ids, all_ids, positions, scope = spatial(engine, request)
    if any(key not in positions for key in keys):
        return None
    if len(keys) > 1 and native.primitive(2, coordinates(scope, positions), len(scope))[0]:
        return None
    reference = engine._fit_circle_reference_for_ids(scope if len(scope) >= 3 else keys,
        positions, proj=request.get("proj"), allow_bbox_fallback=False,
        prefer_shapes=(scope_ids == keys and not engine._clip_has_complete_spatial_snapshot(
            request.get("clip"), all_ids)))
    start = request.get("start_theta")
    return native.ranks(dict(xy=coordinates(keys, positions), mode=0,
        reference=shape_values(reference) if reference is not None else None,
        clockwise=int(direction == "cw"), start_valid=int(start is not None),
        start=float(start) if start is not None else 0.))


def slot_order(engine, request):
    keys = [str(key) for key in (request["base_order"] or [])
            if str(key) in request["actual_positions"]]
    coords = coordinates(keys, request["actual_positions"])
    if len(keys) <= 1:
        return keys
    if native.primitive(2, coords, len(keys))[0]:
        return [keys[index] for index in native.slots(dict(xy=coords, mode=2))]
    reference = engine._fit_circle_reference_for_ids(keys, request["actual_positions"],
        proj=request.get("proj"), prefer_shapes=request.get("prefer_shapes", True),
        allow_bbox_fallback=False)
    result = native.slots(dict(xy=coords, clockwise=1,
        reference=shape_values(reference) if reference is not None else None))
    return [keys[index] for index in result]


def trivial(request):
    return native.ranks(dict(xy=[(0., 0.) for _ in ids(request["ids"])], mode=2))


def spatial_indices(engine, request):
    keys = ids(request["ids"])
    if len(keys) <= 1:
        return native.slots(dict(xy=[(0., 0.) for _ in keys], mode=1)).tolist()
    positions = engine._topview_fixture_positions_for_ids(keys, proj=request.get("proj"),
                                                         clip=request.get("clip"))
    if any(key not in positions for key in keys):
        return None
    lexical = sorted(range(len(keys)), key=lambda index: keys[index])
    coords = coordinates([keys[index] for index in lexical], positions)
    order = native.slots(dict(xy=coords, mode=1))
    return [lexical[index] for index in order]
