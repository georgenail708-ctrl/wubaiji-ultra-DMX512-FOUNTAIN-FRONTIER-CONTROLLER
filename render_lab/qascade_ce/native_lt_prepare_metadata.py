"""Plain source/default/conversion metadata only; no numeric clamp."""
import ctypes as ct
from .native_lt_prepare_types import Packed, Value

PLAIN = (bool, int, float, str, bytes, type(None))
FIELDS = ('light_tracking_enabled', 'light_tracking_source', 'light_tracking_threshold',
          'light_tracking_attack', 'light_tracking_release', 'light_tracking_max_brightness',
          'light_tracking_color')
MISSING = object()


def plain_clip(clip, clip_type):
    if type(clip) is not clip_type:
        return False
    values = object.__getattribute__(clip, '__dict__')
    return type(values) is dict and all(name in values and type(values[name]) in PLAIN for name in FIELDS)


def _integer(packed, index, raw):
    # Reuse native_trajectory_values.encode's exact int serialization recipe.
    number = int(raw)
    data = int.to_bytes(number, (int.bit_length(number) + 8) // 8, 'little', signed=True)
    value = Value(kind=1, selector=index, offset=len(packed.pool), length=len(data))
    packed.pool.extend(data)
    return value


def prepare(clip, attack_default, release_default):
    packed = Packed()
    values = object.__getattribute__(clip, '__dict__')
    defaults = (128, attack_default, release_default, 255)
    names = ('light_tracking_threshold', 'light_tracking_attack',
             'light_tracking_release', 'light_tracking_max_brightness')
    stopped = False
    for index, name in enumerate(names):
        packed.request.values[index] = Value(selector=index)
        if stopped:
            continue
        raw = values[name]
        if index == 3:
            raw = defaults[index] if raw in (None, '') else raw
        else:
            raw = raw or defaults[index]
        try:
            if index in (0, 3):
                value = _integer(packed, index, raw)
            else:
                value = Value(kind=2, selector=index, number=float(raw))
        except Exception as error:
            packed.errors[index] = error
            value, stopped = Value(kind=3, selector=index), True
        packed.request.values[index] = value
    owner = (ct.c_uint8 * len(packed.pool)).from_buffer_copy(packed.pool)
    packed.owner = owner
    packed.request.integers = ct.cast(owner, ct.POINTER(ct.c_uint8))
    packed.request.integer_size = len(packed.pool)
    return packed


def text_color(clip):
    source = str(clip.light_tracking_source or 'pump').strip().lower()
    if source not in ('pump', 'valve'):
        source = 'pump'
    color = str(clip.light_tracking_color or '#FFFFFF').strip().upper()
    stripped = color.lstrip('#')
    white = stripped in ('FFFFFF', 'ffffff', '') or len(stripped) != 6
    if color.startswith('#'):
        color = color[1:]
    if len(color) != 6:
        color = 'FFFFFF'
    try:
        colors = (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
    except Exception:
        colors = (255, 255, 255)
    return source, white, colors


def build_params(kind, result, text):
    source, white, colors = text
    params = object.__new__(kind)
    params.source, params.threshold = source, result.threshold
    params.attack, params.release = result.attack, result.release
    params.color_is_white = white
    params.cr, params.cg, params.cb = colors
    params.max_brightness = result.brightness
    return params
