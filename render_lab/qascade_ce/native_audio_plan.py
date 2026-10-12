"""AudioMod metadata marshaling; channel math and decisions belong to CE."""
import copy
import numpy as np

from .native_audio_api import invoke, _COUNTERS

FEATURES = ("energy", "beat_strength", "band_low", "band_mid", "band_high", "onset_flux")
WAVES = ("Sine", "Square", "Sawtooth", "Triangle", "Random", "Cosine", "Bounce",
         "Expo", "Shark Tooth", "S&H", "Wander")
KINDS = ("slice", "accent", "motion", "color")
LEVELS = frozenset(("r", "g", "b", "w", "dimmer", "pump"))
_FEATURE_PREPARED = None


def enum(value, names, default=-1):
    return names.index(value) if value in names else default


def clip_arrays(state):
    clips, offsets, points = [], [0], []
    for row in state.get("clips", ()):
        clips.append((row["start"], row["end"], row.get("fade_in", -1.),
                      row.get("fade_out", -1.), row.get("wave_freq", 2.),
                      enum(row.get("wave", ""), WAVES)))
        points.extend((p[0], p[1], p[2] if len(p) > 2 else 0.)
                      for p in row.get("points", ()))
        offsets.append(len(points))
    return (np.asarray(clips, np.float64).reshape(-1, 6),
            np.asarray(offsets, np.int64), np.asarray(points, np.float64).reshape(-1, 3))


def envelope(state, position):
    clips, offsets, points = clip_arrays(state)
    result = np.zeros(3, np.float64)
    invoke("math", [6, len(clips)], [position, state.get("attack", 0.),
           state.get("release", 0.), 0., 1., state.get("amount", 1.)],
           [clips, offsets, points, result])
    _COUNTERS["math_work"] += int(result[2])
    return result


def effective_rows(rows, gain):
    values = np.asarray([row["wet"] for row in rows], np.float64)
    result = np.empty(len(values) + 1, np.float64)
    invoke("math", [7, len(values)], [gain], [values, result])
    _COUNTERS["math_work"] += int(result[-1])
    return [dict(row, wet=float(wet)) for row, wet in zip(rows, result[:-1])]


def _processor(row):
    p = row["params"]
    return (enum(row["kind"], KINDS), row["enabled"], row["wet"],
            enum(row["source"], FEATURES), p.get("division", 1.), p.get("duty", .5),
            p.get("phase", 0.), p.get("floor", 0.), p.get("edge", 0.),
            p.get("curve", 1.), p.get("mode") == "duck", p.get("min_scale", 0.),
            p.get("max_scale", 1.), p.get("hue_deg", 0.))


def _targets(rows, plan):
    masks = np.zeros((len(rows), 2, 512), np.uint8)
    axes = np.zeros((len(rows), len(plan.get("axes", ()))), np.uint8)
    rgbs = np.zeros((len(rows), len(plan.get("rgb", ()))), np.uint8)
    for index, row in enumerate(rows):
        for kind in row["targets"]:
            target = 0 if kind in LEVELS else 1 if kind == "valve" else None
            if target is not None:
                masks[index, target, list(plan.get("channels", {}).get(kind, ()))] = 1
        axes[index] = [axis["type"] in row["targets"] for axis in plan.get("axes", ())]
        rgbs[index] = [set(("r", "g", "b")).issubset(row["targets"])] * rgbs.shape[1]
    return masks, axes, rgbs


def _frame_buffers(data, active):
    source = np.ascontiguousarray(data, dtype=np.float32)
    mask = np.ascontiguousarray(active, dtype=np.uint8)
    if source.shape != (512,) or mask.shape != (512,):
        _COUNTERS["unsupported"] += 1
        raise ValueError("native audio requires 512 channel values and active flags")
    return source, mask


def process_chain(data, active, payload):
    rows, plan, features = payload
    source, mask = _frame_buffers(data, active)
    processors = np.asarray([_processor(row) for row in rows], np.float64).reshape(-1, 14)
    targets, axis_on, rgb_on = _targets(rows, plan)
    axes = np.asarray([(axis["coarse"], -1 if axis["fine"] is None else axis["fine"],
                        axis["home"]) for axis in plan.get("axes", ())], np.float64).reshape(-1, 3)
    rgb = np.asarray(plan.get("rgb", ()), np.int64).reshape(-1, 3)
    output = source.copy()
    report = np.zeros(4, np.float64)
    controls = [features.get(key, 0.) for key in FEATURES] + [features.get("beat_phase", 0.)]
    invoke("chain", [len(rows), len(axes), len(rgb), bool(features.get("ready")),
           bool(features.get("has_audio")), bool(plan)], controls,
           [source, mask, processors, targets, axes, axis_on, rgb, rgb_on, output, report])
    return (output if report[0] else data), report


def _curve_rows(raw):
    times, energy, onset, rms, bands = raw[:5]
    n = len(times)
    columns = [values if len(values) == n else [0.] * n for values in (energy, onset, rms)]
    bands = bands if len(bands) == n else [(0., 0., 0.)] * n
    return np.asarray([(*[column[i] for column in columns], *bands[i])
                       for i in range(n)], np.float64).reshape(-1, 6)


def _legacy_rows(raw):
    beats, strength, bands, onset = raw[5:9]
    rows = []
    for i in range(len(beats)):
        band = bands[i] if i < len(bands) else None
        rows.append((strength[i] if i < len(strength) else 1.,
                     *(band[c] if band is not None and c < len(band) else
                       0. if band is not None else 1. for c in range(3)),
                     onset[i] if i < len(onset) else 1.))
    return np.asarray(rows, np.float64).reshape(-1, 5)


def feature_arrays(features):
    """One bounded immutable plan; exact raw-value equality detects in-place edits."""
    global _FEATURE_PREPARED
    fields = ("curve_times", "energy_curve", "onset_curve", "rms_curve", "band_curve",
              "beat_times", "beat_strength", "band_energy", "onset_flux", "section_boundaries")
    raw = tuple(getattr(features, field, None) or [] for field in fields)
    cached = _FEATURE_PREPARED
    if cached is not None and cached[0] is features and cached[1] == raw:
        return cached[2]
    arrays = (np.asarray(raw[0], np.float64), _curve_rows(raw),
              np.asarray(raw[5], np.float64), np.asarray(raw[6], np.float64),
              _legacy_rows(raw), np.asarray(raw[9], np.float64))
    _FEATURE_PREPARED = None
    if sum(array.nbytes for array in arrays) <= 2 * 1024 * 1024:
        _FEATURE_PREPARED = (features, copy.deepcopy(raw), arrays)
    return arrays


def file_controls(features, position):
    times, curves, beats, strength, _legacy, _sections = feature_arrays(features)
    result = np.zeros(10, np.float64)
    invoke("math", [1, len(times), len(beats), len(strength)],
           [position, features.duration, features.bpm], [times, curves, beats, strength, result])
    _COUNTERS["math_work"] += int(result[9])
    return result


def scalar_modulation(state, features, position):
    """Original diagnostic scalar; it does not read or advance rule history."""
    clips, offsets, points = clip_arrays(state)
    beats, series = np.zeros(0, np.float64), np.zeros((0, 5), np.float64)
    if features is not None:
        prepared = feature_arrays(features)
        beats, series = prepared[2], prepared[4]
    result = np.zeros(2, np.float64)
    invoke("math", [5, len(clips), len(beats), enum(state.get("feature"), FEATURES, 0)],
           [position, state.get("attack", 0.), state.get("release", 0.), state.get("amount", 1.)],
           [clips, offsets, points, beats, series, result])
    _COUNTERS["math_work"] += int(result[1])
    return float(result[0])
