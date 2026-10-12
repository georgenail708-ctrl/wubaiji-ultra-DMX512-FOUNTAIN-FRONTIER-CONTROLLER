"""Reuse public ordering and prepared geometry; native CE evaluates the gains."""
import numpy as np

from .native_source_bridge import modules


def _geometry(engine, args):
    clip, universe = args["clip"], args["universe"]
    compiled, fixtures = args["compiled"], args["u_fixtures"]
    if fixtures:
        if compiled is not None:
            return (list(compiled.get("sorted_fixtures", []) or []),
                    list(compiled.get("spans", []) or []), compiled.get("span_idx"))
        ordered, spans = engine._clip_ordered_fixtures_for_fade(clip, universe, fixtures,
            track=args["track"], fixture_defs_by_id=args["fixture_defs_by_id"], proj=args["proj"])
        return ordered, spans, None
    active = engine._get_clip_active_channels(clip, universe, track=args["track"])
    return [], [(int(channel)-1, int(channel)) for channel in active], None


def _span_index(spans, existing):
    if existing is not None:
        return existing
    result = np.full(512, -1, np.int32)
    for index, (start, end) in enumerate(spans):
        if 0 <= start < end <= 512:
            result[start:end] = index
    return result


def _groups(engine, args, fixtures, count, directions):
    motion, _ = modules()
    clip = args["clip"]
    if fixtures:
        return [engine._sequential_fixture_group_data(clip, fixtures, direction, count,
                    track=args["track"], proj=args["proj"]) for direction in directions]
    length = len(engine._get_clip_active_channels(clip, args["universe"], track=args["track"]))
    parallel = str(getattr(clip, "fade_group_sequence_mode", "parallel")).lower() != "sequential"
    return [motion.groups(engine._compute_direction_positions(direction, length), count, parallel)
            for direction in directions]


def _timing(engine, clip, local_t, directions):
    _, clock = modules()
    enabled, groups, pauses, single, batches, step = engine._clip_sequential_timing_detail(clip)
    if step is None:
        raw = [single, getattr(clip, "seq_delay_in", 0.) or 0.,
               getattr(clip, "seq_delay_out", 0.) or 0., getattr(clip, "fade_in", 0.) or 0.,
               getattr(clip, "fade_out", 0.) or 0., getattr(clip, "duration", 0.) or 0.]
        single = float(clock.invoke_scalar(9, [], raw)[0])
        step = single
    flags = [int(directions[0] != "none"), int(directions[1] != "none"), int(enabled), batches, len(pauses)]
    raw = [getattr(clip, key, 0.) or 0. for key in
           ("duration", "fade_in", "fade_out", "seq_delay_in", "seq_delay_out")]
    values = clock.invoke(8, flags, raw+[local_t, step, single], pauses)[0]
    return enabled, groups, pauses, single, batches, step, values


def _ratio_branch(clip):
    from qascade_ce import native_legacy_direction_prepare as legacy
    values = list((getattr(clip, "fade_type_ratio_in", {}) or {}).values())
    values.extend((getattr(clip, "fade_type_ratio_out", {}) or {}).values())
    return legacy.ratio_nondefault([float(value) for value in values])


def bundle(engine, request):
    from . import playback_engine as public
    from qascade_ce import native_legacy_direction_prepare as legacy
    motion, _ = modules()
    args, clip = request["args"], request["args"]["clip"]
    directions = (engine._get_clip_fade_in_direction(clip), engine._get_clip_fade_out_direction(clip))
    enabled, groups, pauses, single, batches, step, values = _timing(engine, clip, args["local_t"], directions)
    global_batches = batches
    fixtures, spans, existing = _geometry(engine, args)
    length = len(fixtures) if args["u_fixtures"] else len(spans)
    metadata = _groups(engine, args, fixtures, groups, directions) if length else [(None, None, None)]*2
    mode = 2 if enabled else 1
    if not args["u_fixtures"]:
        mode = 3 if str(getattr(clip, "fade_group_sequence_mode", "parallel")).lower() == "sequential" else 4
        batches = engine._clip_sequential_batch_count(
            clip, legacy.group_counts(length, groups)[0]) if length else batches
    te, cin, cout, sin, sout = motion.transition_codes(engine, clip)
    config = dict(K=length, batch_count=batches, mode=mode if length else 0, te=te,
        curve_in=cin, curve_out=cout, strategy_in=sin, strategy_out=sout,
        is_in=int(values[5]), is_out=int(values[6]), completed_enabled=int(enabled and length>0),
        ratio_nondefault=int(bool(args["u_fixtures"]) and _ratio_branch(clip)),
        local_t=args["local_t"], duration=values[0], fade_in=values[1], fade_out=values[2],
        delay_in=values[3], delay_out=values[4], single=single, step=step, scale_in=1., scale_out=1.,
        gi_in=metadata[0][0], gp_in=metadata[0][1], gs_in=metadata[0][2],
        gi_out=metadata[1][0], gp_out=metadata[1][1], gs_out=metadata[1][2],
        span_idx=_span_index(spans, existing), pauses=pauses,
        ease_lut=engine._seq_delay_ease_lut(getattr(clip, "seq_delay_curve", None)), motor_no_delay=0)
    if args["u_fixtures"] and public._clip_motor_no_delay(engine, clip):
        cyclic = public._sequential_source_is_cyclic(engine, clip, public._seq_primary_universe(clip))
        if cyclic:
            config["motor_no_delay"] = 1
            config["motor"] = engine._build_motor_channel_mask(args["universe"], args["proj"])
    result = motion.direction(config)
    return _complete_bundle(motion, config, result, (enabled, global_batches))


def _complete_bundle(motion, config, result, context):
    enabled, batches = context
    if enabled and config["batch_count"] != batches:
        done = dict(config, batch_count=batches, mode=0, is_in=0, is_out=0)
        return result[0], motion.direction(done)[1], result[2]
    return result


def gains(engine, request):
    return bundle(engine, request)[0]


def completed(engine, request):
    return bundle(engine, request)[1]


def positions(engine, request):
    motion, _ = modules()
    args = request["args"]
    return motion.positions(args["direction"], args["K"])
