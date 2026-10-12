"""Source-row sampling preserves lookup/cache writes and uses native clamp."""
from .native_programmer_row_queries import quantized
from .native_programmer_row_qualification import parsed


def source_value(context, host, fid, channel):
    panel = context.panel
    cache = getattr(host, "_row_chan_cache", None)
    if cache is None:
        cache = host._row_chan_cache = {}
    key = (str(fid), channel)
    row = cache.get(key)
    if row is None or not panel._qobject_alive(row):
        row = None
        try:
            rows = list(getattr(host._ch_list, "_rows", []) or [])
        except Exception:
            return None
        for candidate in rows:
            if channel in getattr(candidate, "_ch_types", []) and panel._qobject_alive(candidate):
                if str(fid) in getattr(candidate, "_fixture_id_set", set()):
                    cache[key], row = candidate, candidate
                    break
        if row is None:
            for candidate in rows:
                if channel in getattr(candidate, "_ch_types", []) and panel._qobject_alive(candidate):
                    cache[key], row = candidate, candidate
                    break
    if row is None or not panel._qobject_alive(row):
        return None
    try:
        value = (row._layers.get("fader", {}).get(fid, {}) or {}).get(channel)
        if value is None:
            value = row._values.get(fid, {}).get(channel)
        if value is not None:
            if not parsed(value):
                context.statistics.compatibility("unsupported")
                return max(0, min(255, int(round(float(value)))))
            return quantized(context.native, (value,), mode=0)[0][0]
    except Exception:
        return None
    return None
