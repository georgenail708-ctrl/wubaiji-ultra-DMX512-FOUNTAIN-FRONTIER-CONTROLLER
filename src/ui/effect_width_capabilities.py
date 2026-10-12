"""Source capabilities for the shared width consumer, without a second catalog.

Only narrow queries receive transient metadata. Default frames keep the original
kernel parameters; no function or engine is retained by a permanent registry.
"""
from . import effect_width_support as _support
from . import effect_width_native as _native
from .effect_spatial_semantics import width_factor

EXISTING = _support._FAMILIES + (
    "_chase_kernel.<locals>.fn", "_foldcascade_kernel.<locals>.fn")
RETAINED = frozenset({"cur_lf_sparkle_s38", "cur_lf_sparkle_s39", "cur_rand2_sparkle_s129",
                     "cur_rand_sparkle_s12", "cur_vg_probgates1", "cur_vg_sparkles1"})
POINT_KINDS = frozenset({"sh", "cross", "sparkle", "twinkle", "starfield", "probgate"})
MOTORS = frozenset({"motor_x", "motor_y", "motor_z", "pan", "tilt"})
COLORS = frozenset({"R", "G", "B", "W"})
PURE_COLOR = frozenset({"rainbow", "hue_rot", "plasma", "perlin_color", "dualtone", "tricolor"})


def source_kind(fn):
    cells = dict(zip(fn.__code__.co_freevars, fn.__closure__ or ()))
    cell = next((cells[key] for key in ("kind", "variant", "shape") if key in cells), None)
    value = cell.cell_contents if cell is not None else ""
    return str(value) if isinstance(value, (str, int, float)) else getattr(value, "__name__", "")


def point_source(fn, kind):
    return kind in POINT_KINDS or fn.__name__ in {"_random_sparkle", "_light_alternate_strobe"}


def grouped_history(fn):
    if fn.__module__.endswith("effect_presets_v29"):
        return True
    if not fn.__module__.endswith("effect_presets_audio_true"):
        return False
    cells = dict(zip(fn.__code__.co_freevars, fn.__closure__ or ()))
    cell = cells.get("impl")
    name = getattr(cell.cell_contents, "__name__", "") if cell is not None else ""
    return name in {"_f_chord_change_accent", "_f_energy_slope_detector"}


def kernel_params(fn, params, preset_id=None):
    if (width_factor(params) >= .999 or fn.__qualname__.endswith(EXISTING)
            or preset_id in RETAINED or _native.is_base_chase(fn)):
        return _support.kernel_params(fn, params)
    kind = source_kind(fn)
    return dict(params, _width_support_handled=True, _typed_width=True,
                _typed_width_kind=kind, _typed_width_point=point_source(fn, kind))


def _position_condition(fn, params, channels):
    channel = str(params.get("ch_type", params.get("ch", "")))
    targets = set(channels)
    return (channel in MOTORS or "motor" in fn.__module__ or fn.__name__.startswith("_motor")
            or (not channel and bool(targets) and targets <= MOTORS))


def _color_condition(fn, kind):
    return fn.__name__ == "_light_color_chase" or (
        "_color_kernel" in fn.__qualname__ and kind in PURE_COLOR)


def condition(fn, params, channels=()):
    """A visible condition describes source capability, never current brightness."""
    kind = source_kind(fn)
    if fn.__name__ == "_breathing_groups":
        return True, ""
    if _position_condition(fn, params, channels):
        return False, "位置效果：使用原半径、中心和振幅；没有独立亮度支持。"
    if _color_condition(fn, kind):
        return False, "色相效果：保持整行颜色，使用原颜色与速度参数。"
    if fn.__name__ == "_audio_section" and float(params.get("phase_spread", 0) or 0) == 0:
        return False, "全组时间效果：设置相位铺开后才有空间宽度。"
    return True, ""
