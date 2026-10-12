"""Install default geometry numeric entries without changing source or caches."""
import functools
import inspect
import logging

from . import native_source_motion_geometry as native
from src.engine import native_source_bridge_geometry as bridge
from src.engine import native_source_bridge_geometry_slots as slots
from src.engine import native_source_bridge_geometry_segments as segments
from src.engine import native_source_bridge_geometry_groups as groups

log = logging.getLogger(__name__)
_SOURCE_GEOMETRY_PUBLIC_CALL_CNT = [0]
_SOURCE_GEOMETRY_PUBLIC_ERROR_CNT = [0]
_SOURCE_GEOMETRY_PUBLIC_FALLBACK_CNT = [0]
_SOURCE_GEOMETRY_PUBLIC_SUCCESS_CNT = [0]
METHODS = {
    "_direction_positions_for_fixture_ids": (slots.directions, "ids direction proj clip scope_ids start_theta",
        dict(proj=None, clip=None, scope_ids=None, start_theta=None)),
    "_circle_shape_metric_for_fixture": (bridge.metric, "shape pos", {}),
    "_fit_circle_reference_for_ids": (bridge.fit, "ids positions proj prefer_shapes allow_bbox_fallback",
        dict(proj=None, prefer_shapes=True, allow_bbox_fallback=True)),
    "_linear_direction_positions_for_fixture_ids": (bridge.linear, "ids direction proj clip scope_ids",
        dict(proj=None, clip=None, scope_ids=None)),
    "_circle_direction_positions_for_fixture_ids": (bridge.circle, "ids direction proj clip scope_ids start_theta",
        dict(proj=None, clip=None, scope_ids=None, start_theta=None)),
    "_angle_direction_positions_from_virtual_coords": (bridge.angular, "ids direction proj clip scope_ids",
        dict(proj=None, clip=None, scope_ids=None)),
    "_centroid_angle_direction_positions_for_fixture_ids": (bridge.angular, "ids direction proj clip scope_ids",
        dict(proj=None, clip=None, scope_ids=None)),
    "_circle_slot_order_for_virtual_positions": (bridge.slot_order, "base_order actual_positions proj prefer_shapes",
        dict(proj=None, prefer_shapes=True)),
    "_spatial_base_order_indices_for_fixture_ids": (bridge.spatial_indices, "ids proj clip",
        dict(proj=None, clip=None)),
    "_virtual_topview_fixture_positions_for_ids": (slots.virtual, "ordered_ids proj clip scope_ids slot_mode",
        dict(proj=None, clip=None, scope_ids=None, slot_mode="linear")),
    "_order_fixture_ids_for_direction": (slots.ordered, "ids direction proj clip scope_ids start_theta",
        dict(proj=None, clip=None, scope_ids=None, start_theta=None)),
    "_shape_aware_order_segments_for_fade": (segments.segments, "ordered_ids proj clip", dict(proj=None, clip=None)),
    "_infer_order_circle_segments": (segments.inferred, "ordered_ids positions", {}),
    "_sequential_fixture_group_data": (groups.data, "clip local_fixtures direction group_count track proj",
        dict(track=None, proj=None)),
    "_sequential_group_indices_for_positions": (groups.indices, "positions group_count", {}),
}
PRIMITIVES = {
    "_rotate_xy_for_order": (0, "x y angle_deg"),
    "_axis_cluster_count": (1, "values"),
    "_matrix_like_points": (2, "coords"),
    "_matrix_ring_value": (3, "x y cx cy span_x span_y"),
}


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_SOURCE_GEOMETRY_PUBLIC_") and name.endswith("_CNT")}


def _call(owner, original, handler, request):
    _SOURCE_GEOMETRY_PUBLIC_CALL_CNT[0] += 1
    if native.available():
        try:
            result = handler(owner, request)
            _SOURCE_GEOMETRY_PUBLIC_SUCCESS_CNT[0] += 1
            return result
        except Exception:
            _SOURCE_GEOMETRY_PUBLIC_ERROR_CNT[0] += 1
            log.exception("native public geometry failed: %s", request["name"])
    _SOURCE_GEOMETRY_PUBLIC_FALLBACK_CNT[0] += 1
    return original(*request["fallback_args"], **request["keyword"])


def _wrapper(name, original, contract, kind):
    handler, fields, defaults = contract
    names = fields.split()

    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        owner = None if kind == "static" else args[0]
        positional = args if kind == "static" else args[1:]
        values = defaults.copy()
        values.update(zip(names, positional))
        values.update(kwargs)
        values.update(name=name, fallback_args=args, keyword=kwargs)
        return _call(owner, original, handler, values)

    return wrapped


def _primitive(owner, request):
    op, fields = PRIMITIVES[request["name"]]
    if op in (1, 2):
        value = request[fields]
        result = native.primitive(op, value, len(value))[0]
        return bool(result) if op == 2 else int(result)
    values = [float(request[field] or 0.) for field in fields.split()]
    result = native.primitive(op, values)
    return tuple(float(value) for value in result) if op == 0 else float(result[0])


def _install_method(cls, name, contract):
    descriptor = inspect.getattr_static(cls, name)
    if isinstance(descriptor, staticmethod):
        wrapped = _wrapper(name, descriptor.__func__, contract, "static")
        setattr(cls, name, staticmethod(wrapped))
    elif isinstance(descriptor, classmethod):
        wrapped = _wrapper(name, descriptor.__func__, contract, "class")
        setattr(cls, name, classmethod(wrapped))
    else:
        setattr(cls, name, _wrapper(name, getattr(cls, name), contract, "instance"))


def install(engine):
    from src.engine.playback_engine import PlaybackEngine
    if not native.available() and not native.install(engine):
        return False
    if PlaybackEngine.__dict__.get("_ce_source_geometry_installed", False):
        return True
    for name, contract in METHODS.items():
        _install_method(PlaybackEngine, name, contract)
    for name, (_, fields) in PRIMITIVES.items():
        _install_method(PlaybackEngine, name, (_primitive, fields, {}))
    PlaybackEngine._ce_source_geometry_installed = True
    return True
