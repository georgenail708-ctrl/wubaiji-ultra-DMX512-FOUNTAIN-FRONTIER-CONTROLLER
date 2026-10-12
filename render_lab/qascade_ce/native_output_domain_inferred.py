"""Default public inferred-mask dispatch, preserving the original source body."""
import functools

from src.engine.native_inferred_mask import current_mask

_ORIGINAL = None
_FIELDS = ("clip", "universe", "source_t", "base_active", "current_raw", "hold_extension")


def _request(positional, keyword):
    if len(positional) > len(_FIELDS):
        return None
    values = dict(zip(_FIELDS, positional))
    if any(name in values or name not in _FIELDS for name in keyword):
        return None
    values.update(keyword)
    if not all(name in values for name in _FIELDS[:4]):
        return None
    return dict(current_raw=None, hold_extension=False) | values


def _dispatch(engine, positional, keyword):
    from . import native_output_domain as domain
    request = _request(positional, keyword)
    if request is None:
        return _ORIGINAL(engine, *positional, **keyword)
    if domain.NATIVE is not None:
        errors = domain.COUNTERS["errors"]
        try:
            return current_mask(engine, request)
        except Exception:
            if domain.COUNTERS["errors"] == errors:
                domain.COUNTERS["errors"] += 1
    domain.note_fallback()
    return _ORIGINAL(engine, *positional, **keyword)


def install():
    global _ORIGINAL
    from src.engine.playback_engine import PlaybackEngine
    if _ORIGINAL is not None:
        return
    _ORIGINAL = PlaybackEngine._clip_current_channel_active_mask

    @functools.wraps(_ORIGINAL)
    def native_inferred(engine, *args, **kwargs):
        return _dispatch(engine, args, kwargs)

    PlaybackEngine._clip_current_channel_active_mask = native_inferred
