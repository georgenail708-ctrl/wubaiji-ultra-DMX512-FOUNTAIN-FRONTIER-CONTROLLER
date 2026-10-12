"""Original source-state containers with native scalar clocks and scores."""
import numpy as np


def modules():
    from qascade_ce import native_source_motion, native_source_motion_clock
    return native_source_motion, native_source_motion_clock


def scalar(engine, request):
    motion, clock = modules()
    name, args = request["name"], request["args"]
    clip = args["clip"]
    functions = {
        "_clip_motor_force_profile": lambda: clock.profile(engine, clip),
        "_clip_motor_force_source_time": lambda: clock.force_time(
            engine, clip, args["source_t"], args["source_duration"]),
        "_clip_playback_rate": lambda: clock.playback_rate(clip),
        "_clip_source_duration": lambda: clock.source_duration(clip, args["timeline_duration"]),
        "_clip_motor_phase_amount": lambda: clock.phase_amount(engine, clip),
        "_clip_fade_gain": lambda: motion.fade(engine, clip, args["local_t"]),
    }
    return functions[name]()


def active_batch(engine, request):
    _, clock = modules()
    args = request["args"]
    step = args["single"] if args["step"] is None else args["step"]
    return clock.active_batch(args["local_t"], args["single"], args["pauses"],
                              dict(batches=args["batch_count"], step=step))


def sequential_time(engine, request):
    from . import playback_engine as public
    _, clock = modules()
    args, clip = request["args"], request["args"]["clip"]
    enabled, _, pauses, single, batches, step = engine._clip_sequential_timing_detail(clip)
    step = single if step is None else step
    cycle = public._recorded_cycle(engine, clip)
    wing = public._sequential_has_wing(clip)
    config = dict(enabled=int(enabled), batches=batches, pause_count=len(pauses), pauses=pauses,
        wing=int(wing), resize=int(bool(getattr(clip, "resize_cycle", True))),
        motor_no_delay=int(public._clip_motor_no_delay(engine, clip)),
        cyclic=int(public._sequential_source_is_cyclic(engine, clip, public._seq_primary_universe(clip))),
        local_t=args["local_t"], duration=args["source_duration"],
        rate=engine._clip_playback_rate(clip), single=single, step=step,
        hold_end=cycle[2] if cycle is not None else args["source_duration"])
    return clock.sequential_time(config)


def run_mode(engine, request):
    from . import playback_engine as public
    _, clock = modules()
    args, clip = request["args"], request["args"]["clip"]
    mode = engine._clip_run_mode(clip)
    cycle = public._recorded_cycle(engine, clip)
    eligible = cycle is not None
    if mode == "normal":
        eligible = eligible and clock.extent(clip, cycle)
    first, period, last = cycle or (0., 0., 0.)
    config = dict(mode=clock.MODES[mode], resize=int(bool(getattr(clip, "resize_cycle", True))),
        cycle_eligible=int(eligible), source_t=args["source_t"], duration=args["source_duration"],
        recorded_span=engine._clip_recorded_source_span(clip, args["source_duration"]),
        first=first, period=period, last=last)
    return clock.run_mode(config)


def table(engine, clip, universe, compiled=None):
    if compiled is not None:
        times, frames = compiled.get("frame_times"), compiled.get("frame_data")
        if times is not None and frames is not None:
            return dict(times=times, frames=frames)
    cached = engine._clip_mask_frames_cached(clip, universe)
    if cached is not None:
        return dict(times=cached[0], frames=cached[1])
    frames = engine._clip_frame_index_for_universe(clip, universe)
    from .clip_mask_prep import _frame_row
    timestamps = [float(getattr(frame, "timestamp", 0.) or 0.) for frame in frames]
    values = [_frame_row(getattr(frame, "data", None)) for frame in frames]
    return dict(times=np.asarray(timestamps, np.float64),
                frames=np.asarray(values, np.uint8).reshape(len(values), 512))


def scores(engine, request):
    motion, _ = modules()
    args, clip = request["args"], request["args"]["clip"]
    config = dict(start=args["clip_start"], rate=engine._clip_playback_rate(clip),
                  active=_score_mask(args["active"]), hold_cap=np.inf)
    compiled = args.get("compiled")
    if compiled is not None:
        index = engine._compiled_clip_frame_index(compiled, args["source_t"])
        changes = compiled.get("last_change_time")
        if index < 0:
            return motion.scores_row(dict(config, changes=None))
        if changes is not None and index < len(changes):
            config["changes"] = changes[index]
            config["hold_cap"] = _score_cap(clip, args)
            return motion.scores_row(config)
    config.update(table(engine, clip, args["universe"]))
    config.update(source_t=args["source_t"], start=args["clip_start"],
                  rate=engine._clip_playback_rate(clip), hold_cap=_score_cap(clip, args),
                  active=config["active"])
    return motion.scores(config)


def _score_mask(active):
    result = np.asarray(active, dtype=bool)
    if result.shape != (512,):
        padded = np.zeros(512, dtype=bool)
        count = min(result.size, 512)
        padded[:count] = result.reshape(-1)[:count]
        result = padded
    return result


def _score_cap(clip, args):
    if not args["hold_extension"]:
        return np.inf
    _, clock = modules()
    return clock.hold_cap(clip, args["clip_start"])


def force_frame(engine, request):
    motion, clock = modules()
    args, clip = request["args"], request["args"]["clip"]
    parts = clock.profile(engine, clip)
    if parts is None:
        return args["raw"]
    compiled = args["compiled"]
    config = table(engine, clip, args["universe"], compiled)
    motor = compiled.get("motor_force_mask")
    if motor is None:
        motor = engine._build_motor_channel_mask(args["universe"], args["proj"])
        motor = engine._expand_motor_pair_mask_for_universe(args["universe"], motor, proj=args["proj"])
        compiled["motor_force_mask"] = motor
    config.update(source_t=args["source_t"], duration=args["source_duration"],
                  accel=parts[0], cruise=parts[1], decel=parts[2], motor=motor, raw=args["raw"])
    return motion.force(config)[0]
