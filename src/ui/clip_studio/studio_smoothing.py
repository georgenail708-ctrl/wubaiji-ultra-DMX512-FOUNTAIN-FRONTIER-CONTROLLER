"""R35 thin Studio adapters for the existing offline transaction and Clip field.

Same ParamSpec controls, compiler, reverse editor and application ledger; no
second frame processor, history stack, hidden defaults or automatic playback.
"""
from ...engine.clip_smoothing_profiles import control_defaults, offline_description
from ...engine.output_smoother import BUILTIN_SMOOTH_PRESETS


def parameter_rows(preset):
    if preset.id == "bypass":
        return ()
    defaults = control_defaults(preset)
    return (("strength", "平滑强度", defaults["strength"], 0., 1., .05, "×"),
            ("window_seconds", "处理时间窗", defaults["window_seconds"], .01, 5., .01, "秒"),
            ("detail_threshold", "忽略小抖动", defaults["detail_threshold"], 0., 255., .5, "DMX"))


def description(preset):
    return offline_description(preset)


def compile_node(node):
    pid = str(node.get("key", ""))[len("smooth_"):]
    preset = BUILTIN_SMOOTH_PRESETS.get(pid)
    fields = {"smooth_preset": pid, "smooth_preset_name": preset.name if preset else pid}
    # Old sheets without controls must not overwrite saved custom Clip settings.
    custom = dict(node.get("params") or {})
    if custom and pid != "bypass":
        fields["smooth_custom_params"] = custom
    return fields


def reverse_params(clip):
    return dict(getattr(clip, "smooth_custom_params", None) or {})


def apply(context):
    preset = str(context.eff.get("smooth_preset", context.prev.get("smooth_preset",
                 getattr(context.clip, "smooth_preset", ""))) or "")
    if not preset:
        return []
    custom = context.eff.get("smooth_custom_params")
    success = _invoke(context, preset, custom)
    expected = "" if preset == "bypass" else preset
    matched = str(getattr(context.clip, "smooth_preset", "") or "") == expected
    custom_matched = _custom_matches(context, preset, custom)
    if success is False or not matched or not custom_matched:
        return [key for key in ("smooth_preset", "smooth_custom_params") if key in context.eff]
    return []


def _invoke(context, preset, custom):
    if custom is None:
        return context.tw._on_clip_auto_smooth(context.cid, preset)
    return context.tw._on_clip_auto_smooth(context.cid, preset, dict(custom))


def _custom_matches(context, preset, custom):
    if custom is None:
        return True
    expected = {} if preset == "bypass" else custom
    return dict(getattr(context.clip, "smooth_custom_params", None) or {}) == expected
