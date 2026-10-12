"""First-party qualification before original callbacks or published state."""
from .native_curve_scalar_metadata import NUMERIC, accepts, plain_fields
from .native_curve_scalar_install import _default as scalar_default

CURVE_FIELDS = ("duration", "loop", "curve", "points", "cuts", "segments",
                "boundaries", "targets")
SEGMENT_FIELDS = ("groups", "group_batches", "group_pause", "group_pauses",
                  "group_mode", "direction", "transition", "spatial_scope",
                  "wings", "block", "wing_mirror", "wing_reverse")


def segment_safe(owner, segment_type):
    if owner is None:
        return True
    if type(owner) is not segment_type or not plain_fields(owner, SEGMENT_FIELDS):
        return False
    if any(name in owner.__dict__ for name in
           ("effective_batches", "batch_offsets_seconds", "sequential_span_seconds")):
        return False
    return (type(owner.groups) in NUMERIC and type(owner.group_batches) in NUMERIC
            and type(owner.group_pause) in NUMERIC and type(owner.transition) in NUMERIC
            and type(owner.group_pauses) in (list, tuple)
            and all(type(v) in NUMERIC for v in owner.group_pauses))


def _curve_methods(owner, hooks):
    return (not any(name in owner.__dict__ for name in hooks.canonical)
            and all(getattr(type(owner), name) is function
                    for name, function in hooks.canonical.items()))


def _segment_default(owner, hooks):
    return (segment_safe(owner, hooks.segment)
            and all(getattr(hooks.segment, name) is function
                    for name, function in hooks.segment_canonical.items()))


def _curve_lists(owner, hooks):
    lists = (owner.cuts, owner.segments, owner.boundaries)
    return (all(type(rows) in (list, tuple) for rows in lists)
            and all(type(cut) in NUMERIC for cut in owner.cuts)
            and all(_segment_default(item, hooks) for item in (*owner.segments, *owner.boundaries)))


def curve_safe(owner, hooks):
    if type(owner) is not hooks.curve or not plain_fields(owner, CURVE_FIELDS):
        return False
    if not scalar_default(3, owner) or not accepts(3, owner, 0.):
        return False
    return _curve_methods(owner, hooks) and _curve_lists(owner, hooks)


def _ids_safe(arguments):
    ids = arguments.get("ids", arguments.get("ids_left_to_right", ()))
    return (type(ids) in (list, tuple)
            and all(type(fid) in (str, int) for fid in ids))


def _partition_safe(arguments):
    return (type(arguments["n_units"]) is int and arguments["n_units"] >= 0
            and type(arguments["batch_count"]) in NUMERIC)


def _timing_safe(arguments):
    if "f" in arguments and type(arguments["f"]) not in (bool, int, float, str, bytes, type(None)):
        return False
    duration = arguments.get("seg_dur", arguments.get("window_dur"))
    return duration is None or type(duration) in NUMERIC


def _shared_owner(arguments, hooks):
    if "cls" in arguments and arguments["cls"] is not hooks.curve:
        return False
    if not _ids_safe(arguments):
        return False
    owner = arguments.get("seg", arguments.get("bseg"))
    if not _segment_default(owner, hooks):
        return False
    positions = arguments.get("positions")
    return positions is None or type(positions) is dict


def shared_safe(arguments, hooks):
    if "n_units" in arguments:
        return _partition_safe(arguments)
    return _shared_owner(arguments, hooks) and _timing_safe(arguments)


def _curve_argument_safe(name, arguments, owner):
    if "t" not in arguments:
        return True
    if name == "_evaluate_shape" and owner.loop and type(arguments["t"]) not in NUMERIC:
        return False
    return accepts(1, owner, arguments["t"])


def qualified(name, arguments, hooks):
    owner = arguments.get("self")
    if owner is None:
        return shared_safe(arguments, hooks)
    if type(owner) is hooks.segment:
        return (_segment_default(owner, hooks)
                and ("per_group" not in arguments or type(arguments["per_group"]) in NUMERIC))
    if not curve_safe(owner, hooks):
        return False
    return _curve_argument_safe(name, arguments, owner)
