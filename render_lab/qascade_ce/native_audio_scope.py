"""Original scoped metadata/history containers; every numerical step is native."""
import numpy as np

from .native_audio_api import available, invoke, _COUNTERS
from .native_audio_plan import FEATURES, WAVES, clip_arrays, enum, feature_arrays

SIGNALS = FEATURES + ("bpm_phase", "band_ratio", "section")
UNITS = ("beat", "bars", "dotted", "triplet", "sixteenth", "hz")


def _number(value, default=0.):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _rule(row):
    p = row.get("params") if isinstance(row.get("params"), dict) else {}
    signal = "onset_flux" if p.get("signal") == "onset" else p.get("signal", "beat_strength")
    return (enum(signal, SIGNALS, 0), _number(p.get("freq"), 2.),
            _number(p.get("depth"), 1.), _number(p.get("attack")),
            _number(p.get("release")), _number(p.get("phase")),
            _number(p.get("invert")), _number(p.get("threshold")),
            _number(p.get("smoothing")), enum(str(p.get("wave", "")), WAVES),
            enum(str(p.get("freq_unit") or "beat"), UNITS, 0),
            2 if p.get("random_mode") == "slew" else int(bool(p.get("random_mode"))),
            _number(p.get("threshold_close")), _number(p.get("hold_ms")))


def _rules(state, types):
    from src.engine.audio_mod_draw_compile import _match_targets, _target_lut
    sources = {str(s["id"]): s for s in state.get("sources", ())
               if isinstance(s, dict) and s.get("id")}
    names = list(sources)
    descriptors = np.asarray([(enum("onset_flux" if sources[n].get("feature") == "onset"
                                   else sources[n].get("feature", ""), SIGNALS, -1),
                               _number(sources[n].get("phase"))) for n in names], np.float64).reshape(-1, 2)
    rows, offsets, indexes = [], [0], []
    target = np.zeros((len(state.get("rules", ())), len(types)), np.uint8)
    lut = _target_lut(set(types))
    for index, row in enumerate(state.get("rules", ())):
        row = row if isinstance(row, dict) else {}
        rows.append(_rule(row))
        ids = [str(s) for s in row.get("source_ids", ()) if s]
        indexes.extend(names.index(n) if n in names else -1 for n in ids)
        offsets.append(len(indexes))
        hits = _match_targets(row.get("ch_types") or [row.get("ch_type", "")], lut)
        for hit in hits:
            target[index, types.index(hit)] = 1
    return (np.asarray(rows, np.float64).reshape(-1, 14), descriptors,
            np.asarray(offsets, np.int64), np.asarray(indexes, np.int64), target)


def _features(features):
    if features is None:
        return np.zeros(0, np.float64), np.zeros((0, 5), np.float64), np.zeros(0, np.float64), 120., 0.
    _times, _curves, beats, _strength, rows, boundaries = feature_arrays(features)
    return beats, rows, boundaries, _number(features.bpm) or 120., _number(features.duration)


def _history(state, history):
    from src.engine.audio_modulation import rules_key
    key = rules_key(state)
    reset = key != history["key"]
    count = len(state.get("rules", ())) * 64
    values = np.zeros((count, 4), np.float64)
    flags = np.zeros((count, 5), np.uint8)
    original = {} if reset else history["vals"]
    for slot, row in original.items():
        if 0 <= slot < count:
            flags[slot, :3] = (1, row.get("y") is not None, row.get("fy") is not None)
            values[slot, :2] = (_number(row.get("y")), _number(row.get("fy")))
            gate = row.get("gate")
            if isinstance(gate, tuple):
                flags[slot, 3] = 1
                values[slot, 2:] = gate
    return values, flags, key, reset


def _write_history(history, packed, result):
    values, flags, key, reset = packed
    if not result[2]:
        return
    if reset:
        history.update(key=key, t=None, vals={})
    history["t"] = float(result[3])
    for slot in np.flatnonzero(flags[:, 4]).tolist():
        row = history["vals"].setdefault(slot, {})
        row["y"] = float(values[slot, 0]) if flags[slot, 1] else None
        row["gate"] = (bool(values[slot, 2]), float(values[slot, 3])) if flags[slot, 3] else None
        row["fy"] = float(values[slot, 1]) if flags[slot, 2] else None


def _addresses(outputs, scope, types):
    universes = list(outputs)
    global_mask = np.zeros((len(universes), 512), np.uint8)
    ct_mask = np.zeros((len(types), len(universes), 512), np.uint8)
    ordinal = np.full((len(universes), 512), -1, np.int64)
    admitted = np.zeros(len(universes), np.uint8)
    ords = scope.get("ordinals")
    for i, universe in enumerate(universes):
        addresses = scope["by_uni"].get(int(universe))
        if addresses is not None:
            admitted[i] = 1
            global_mask[i, addresses] = 1
        for j, kind in enumerate(types):
            ct_mask[j, i, scope["ct"].get(universe, {}).get(kind, [])] = 1
        if ords is not None and addresses is not None and universe in ords[0]:
            ordinal[i, addresses] = ords[0][universe]
    return universes, global_mask, ct_mask, ordinal, admitted


def render_scope(outputs, payload, owners):
    """Atomic native math precedes original ContextVar dictionary publication."""
    if not available():
        return None
    state, features, position = payload
    scope, history = owners
    types = list(scope.get("types", ()))
    if not types:
        types = list(dict.fromkeys(k for row in scope["ct"].values() for k in row))
    rows, sources, source_off, source_idx, target = _rules(state, types)
    clips, point_off, points = clip_arrays(state)
    beats, series, sections, bpm, duration = _features(features)
    values, flags, key, reset = packed = _history(state, history)
    unis, global_mask, ct_mask, ordinal, admitted = _addresses(outputs, scope, types)
    original = np.asarray([outputs[u][0] for u in unis], np.uint8).reshape(-1, 512)
    result_data = original.copy()
    result = np.zeros(5, np.float64)
    ords = scope.get("ordinals")
    count = ords[1] if ords is not None else 0
    integers = [len(unis), len(clips), len(rows), len(types), count, len(beats),
                len(sources), not reset and history["t"] is not None, len(sections),
                enum(state.get("feature", "beat_strength"), FEATURES, 0)]
    scalars = [position, state.get("attack", 0.), state.get("release", 0.),
               state.get("amount", 1.), state.get("spread_deg", 0.),
               state.get("phase_from_deg", 0.), state.get("phase_to_deg", 0.),
               bpm, duration, 0. if reset or history["t"] is None else history["t"]]
    gains = np.zeros((len(types), 64), np.float64)
    present = np.zeros((len(types), 64), np.uint8)
    offsets = np.zeros(count, np.float64)
    buckets = np.zeros(count, np.int64)
    clip_gain = np.zeros(count, np.float64)
    arrays = [result_data, global_mask, ct_mask, ordinal, clips, point_off, points,
              beats, series, sections, rows, sources, source_off, source_idx, target,
              values, flags, admitted, gains, present, offsets, buckets, clip_gain, result]
    invoke("scope", integers, scalars, arrays)
    _write_history(history, packed, result)
    _COUNTERS["scope_work"] += int(result[0])
    _COUNTERS["math_work"] += int(result[1])
    if result[4]:
        return outputs
    return {u: (result_data[i], outputs[u][1]) for i, u in enumerate(unis)}
