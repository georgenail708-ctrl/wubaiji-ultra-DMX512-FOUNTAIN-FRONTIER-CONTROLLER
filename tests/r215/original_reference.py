import numpy as np

class OriginalTargetReference:
    def _get_fade_out_target(self, universe: int, proj=None) -> np.ndarray:
        """Return the idle frame a clip should fade out toward for one universe."""
        universe = int(universe)
        default_entry = None
        nm = self._nm
        if nm is not None:
            try:
                default_entry = (getattr(nm, "_channel_defaults", {}) or {}).get(universe)
            except Exception:
                default_entry = None
        default_sig = None
        if default_entry is not None:
            try:
                data, active = default_entry
                data_arr = np.asarray(data, dtype=np.uint8).reshape(-1)
                active_arr = np.asarray(active, dtype=bool).reshape(-1)
                default_sig = (
                    int(data_arr.size),
                    int(active_arr.size),
                    int(np.sum(data_arr[:min(data_arr.size, 512)].astype(np.uint32))),
                    int(np.count_nonzero(active_arr[:min(active_arr.size, 512)])),
                )
            except Exception:
                default_sig = ("custom", id(default_entry))
        cache_key = (
            "__fade_out_target__",
            universe,
            id(proj),
            len(getattr(proj, "fixtures", []) or []) if proj is not None else 0,
            len(getattr(proj, "fixture_defs", []) or []) if proj is not None else 0,
            default_sig,
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            return np.asarray(cached, dtype=np.float32).copy()
        target = np.zeros(512, dtype=np.float32)
        default_active = np.zeros(512, dtype=bool)

        if default_entry is not None:
            try:
                data, active = default_entry
                data_arr = np.asarray(data, dtype=np.float32)
                if data_arr.shape != (512,):
                    fixed = np.zeros(512, dtype=np.float32)
                    n = min(data_arr.size, 512)
                    if n > 0:
                        fixed[:n] = data_arr.reshape(-1)[:n]
                    data_arr = fixed
                active_arr = np.asarray(active, dtype=bool)
                if active_arr.shape != (512,):
                    fixed_active = np.zeros(512, dtype=bool)
                    n = min(active_arr.size, 512)
                    if n > 0:
                        fixed_active[:n] = active_arr.reshape(-1)[:n]
                    active_arr = fixed_active
                target[active_arr] = data_arr[active_arr]
                default_active |= active_arr
            except Exception:
                pass

        motor_mask = self._build_motor_channel_mask(universe, proj)
        if (motor_mask).any():
            target[motor_mask & ~default_active] = 128.0
        self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in target))
        return target
    def _build_motor_channel_mask(self, universe: int, proj) -> np.ndarray:
        """[LTP 过渡] 返回该 universe 下所有电机通道（motor_x/y/z）的 bool[512] 掉码。

        通过 proj.fixtures + proj.fixture_defs 计算每个 DMX 地址对应的通道类型。
        如果项目无设备信息，返回全 False 掩码（即所有通道起点为 0）。
        """
        universe = int(universe)
        # B(R51 身份缓存纪律): 旧 cache_key 只有 id(proj)+len, 连列表 id 都没有 —
        # 值里存 proj/fixtures/fixture_defs 强引用, 命中处 `is` 复核后使用。
        # （多台设备但列表被替换/对象回收后 id() 复用都不会再错误命中。）
        fixtures = getattr(proj, "fixtures", []) or []
        fixture_defs = getattr(proj, "fixture_defs", []) or []
        cache_key = (
            "__motor_channel_mask__",
            universe,
            id(proj),
            len(fixtures),
            len(fixture_defs),
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if (cached is not None and cached[0] is proj
                and cached[1] is fixtures and cached[2] is fixture_defs):
            return np.asarray(cached[3], dtype=bool).copy()
        MOTOR_TYPES = {"motor_x", "motor_y", "motor_z"}
        mask = np.zeros(512, dtype=bool)
        if proj is None:
            return mask
        defs_by_id = {str(getattr(fd, "id", "")): fd for fd in fixture_defs}
        for fi in fixtures:
            if int(getattr(fi, "universe", 1)) != universe:
                continue
            start = int(getattr(fi, "start_channel", 1)) - 1  # 转 0-indexed
            if not (0 <= start < 512):
                continue
            fd = defs_by_id.get(str(getattr(fi, "def_id", "")))
            if fd is None:
                continue
            ch_types: list = getattr(fd, "channels", []) or []
            for offset, ch_type in enumerate(ch_types):
                addr = start + offset
                if addr >= 512:
                    break
                if str(ch_type).lower() in MOTOR_TYPES:
                    mask[addr] = True
        self._store_clip_fade_geometry_cache(
            cache_key, (proj, fixtures, fixture_defs, tuple(bool(v) for v in mask)))
        return mask
    def _store_clip_fade_geometry_cache(self, key: tuple, value: tuple):
        self._clip_fade_geometry_cache[key] = value
        if len(self._clip_fade_geometry_cache) > 8192:
            self._evict_oldest(self._clip_fade_geometry_cache)
    def _evict_oldest(self, cache: dict) -> None:
        """Drop the oldest entry from a bounded cache, race-safe.

        The background precompile daemon (_start_background_precompile) and the
        gen/render thread both run this write-then-evict path concurrently.
        ``d.pop(next(iter(d)))`` is a non-atomic read-modify-write: two threads
        can race between ``iter`` and ``next`` (RuntimeError: dictionary changed
        size during iteration) or pop the same first key (KeyError). Insertions
        elsewhere keep the cache bounded, so swallowing a lost eviction race is
        harmless — at worst one extra entry survives until the next insert.
        """
        try:
            cache.pop(next(iter(cache)))
        except (KeyError, StopIteration, RuntimeError):
            pass
