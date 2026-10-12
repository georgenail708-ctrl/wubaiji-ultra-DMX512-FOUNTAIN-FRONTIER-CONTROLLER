"""Native audio math thin dispatch retaining original project and state owners."""
import time
from types import MappingProxyType

import numpy as np


def binding():
    try:
        from qascade_ce import native_audio_api
    except ImportError:
        return None
    return native_audio_api if native_audio_api.available() else None


def _empty(position, mode, epoch):
    from .audio_mod_feature_view import CONTROL_KEYS
    result = dict.fromkeys(CONTROL_KEYS, 0.)
    result.update(ready=False, has_audio=False, source=mode, epoch=epoch,
                  reason="live_waiting", time=position, bpm=120., beat_phase=0.)
    return result


def _file_view(project, position, source):
    from . import audio_mod_feature_view as view
    from qascade_ce.native_audio_plan import file_controls, FEATURES
    mode, path, epoch = source
    path = path or str(getattr(project, "audio_path", "") or "")
    result = _empty(position, mode, (epoch, path, view.file_stamp(path)))
    features = view.peek_features(path)
    if features is None or not features.curve_times:
        result["reason"] = view.feature_status(path) if features is None else "features_unavailable"
        return result
    values = file_controls(features, position)
    result.update(zip(FEATURES, values[:6].tolist()))
    result.update(ready=True, has_audio=bool(values[8]), bpm=float(values[6]),
                  beat_phase=float(values[7]), reason="outside_audio")
    if values[9]:
        result["reason"] = "ready" if result["has_audio"] else "silence"
    return result


def feature_view(project, position, offline):
    from . import audio_mod_feature_view as view
    api = binding()
    if api is None:
        return None
    source = view._SOURCE
    if source[0] == "file":
        return MappingProxyType(_file_view(project, position, source))
    result = _empty(position, source[0], source[2])
    live = view._LIVE
    result["reason"] = "offline_live" if offline else "live_waiting"
    allowed = np.zeros(1, np.float64)
    api.invoke("math", [4, 1, offline, live.get("epoch") == source[2]],
               [time.monotonic(), live.get("received_at", 0.)], [allowed])
    if not allowed[0]:
        if not offline and live.get("epoch") == source[2]:
            result["reason"] = "live_expired"
        return MappingProxyType(result)
    result.update({key: live[key] for key in result})
    return MappingProxyType(result)


def context_math(engine, project, position, raw, rows):
    api = binding()
    if api is None:
        return None
    from .audio_modulation import normalize_state
    from qascade_ce.native_audio_plan import envelope, effective_rows
    gain = float(envelope(normalize_state(raw), position)[0]) if raw.get("clips") else 1.
    if gain <= 0.0:
        return gain, rows, None
    features = feature_view(project, position, bool(getattr(engine, "_timeline_render_cache_building_lock", False)))
    return gain, effective_rows(rows, gain), features


def diagnostic_value(state, features, position):
    if binding() is None:
        return None
    from qascade_ce.native_audio_plan import scalar_modulation
    return scalar_modulation(state, features, position)


def update_feedback(feedback):
    api = binding()
    if api is None:
        return False
    report = api.report()
    if report is None:
        return False
    output = np.zeros(3, np.float64)
    api.invoke("math", [3, int(report[0]), int(report[3])],
               [feedback["input_peak"], feedback["output_peak"], feedback["processed_clips"],
                report[1], report[2]], [output])
    feedback.update(input_peak=float(output[0]), output_peak=float(output[1]),
                    processed_clips=int(output[2]))
    return True


def scoped_outputs(outputs, context):
    if binding() is None:
        return None
    from .audio_mod_driver import _resolve_features, _ct_index, _rule_targets
    from .audio_mod_spread import address_ordinals
    from .audio_mod_draw_dsp import _RULE_HISTORY
    from qascade_ce.native_audio_scope import render_scope
    state, project = context["state"], context["project"]
    scope = {"by_uni": context["by_uni"],
             "ct": {u: _ct_index(project, state, u) for u in outputs},
             "types": list(_rule_targets(state)),
             "ordinals": address_ordinals(project, state, context["by_uni"])}
    features = _resolve_features(context["audio_path"])
    return render_scope(outputs, (state, features, context["pos"]), (scope, _RULE_HISTORY.get()))
