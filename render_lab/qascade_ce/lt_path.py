# -*- coding: utf-8 -*-
"""K4/K5/K6 灯跟踪 C 内核 Python 侧装配（编译期 + 每帧束）。

分层（全部不依赖 src/, 离线探针/字节对拍框架直用）:
  * 编译期: build_entries_and_cross + build_scope_blob → LtScope（(clip, universe)
    扁平块, 与 ce_engine.c _lt_scope_parse 逐字节对齐）。槽注册表
    state_slots/color_slots 跨 scope 共享（引擎级状态, 跨 clip 续平滑）。
  * 引擎级: LtEngineState — 槽数组(state/state_t/exists/color/valid) + 键表
    （state_keys/color_keys 供 touched 回写 dict）。
  * 每帧: build_lt_bundle → LtFrameBundle — scope_off[n_tasks] + 每任务指针
    数组（nm_rgb/fx_live/cross_tbl/cross_t0/cross_live）+ 引擎状态引用,
    直喂 ce_timeline_frame 尾段 16 参（TimelineCtx._lt_args 同构）。

语义红线（与 playback_engine.py _apply_clip_light_tracking :4150-4650 逐字节）:
  * 泵值优先级 = 本 clip 帧 > 其他活跃 clip 帧(alt, 泵 clip 自身 start 对齐)
    > 实时回退(exclude clip 层); 泵=0 → 写 0 灭。
  * 跨域跳过: clip 有域集且泵域∉域集且无帧表 → 跳过（零状态）。
  * 帧选择 = 最后 timestamp<=pt_local 的帧, 全 > → 第 0 帧（非最近邻）;
    size 守卫用泵帧原始长度（非 512 填充）。
  * 颜色: raw RGB/W 非零覆盖缓存; 白模式仅当槽无效且 raw 全零时 NM 采样
    （C 内核自持 raw 写 → color_valid 判定, 包装层无条件采样无害）。
  * 状态槽: F5 与跨域同键（同键双更新, 第二次 dt=0.001 小步）。

env 门控: QASCADE_CE_LT 默认 "0"（变更区纪律, _mount_ce.py 登记）。
"""

from __future__ import annotations

import ctypes
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "LtParams", "LtScope", "LtEngineState", "LtFrameBundle",
    "build_entries_and_cross", "build_scope_blob", "build_lt_bundle",
    "lt_scope_off_for_tasks", "make_pump_tbl", "PumpTbl",
]

_UNI = 512
_HDR = 1168
_FX_ROW = 91       # int32 / 主行
_CX_ROW = 21       # int32 / 跨域行

_PC_I = ctypes.POINTER(ctypes.c_int)
_PC_U8 = ctypes.POINTER(ctypes.c_uint8)
_PC_D = ctypes.POINTER(ctypes.c_double)
_PC_V = ctypes.POINTER(ctypes.c_void_p)


class LtParams:
    """clip 级灯跟踪参数（引擎 _apply_clip_light_tracking :4158-4178 归一）。"""

    __slots__ = ("source", "threshold", "attack", "release",
                 "color_is_white", "cr", "cg", "cb", "max_brightness")

    def __init__(self, source="pump", threshold=128, attack=0.08, release=0.22,
                 color_is_white=True, cr=255, cg=255, cb=255, max_brightness=255):
        self.source = str(source or "pump").strip().lower()
        if self.source not in ("pump", "valve"):
            self.source = "pump"
        self.threshold = int(max(0, min(255, int(threshold or 128))))
        self.attack = float(max(0.01, float(attack or 0.08)))
        self.release = float(max(0.01, float(release or 0.22)))
        self.color_is_white = bool(color_is_white)
        self.cr = int(cr)
        self.cg = int(cg)
        self.cb = int(cb)
        if max_brightness in (None, ""):
            max_brightness = 255
        # 0 是合法输入(对话框 0-255), `or 255` 会把 0 反转成全亮(EDGE-03)。
        self.max_brightness = int(max(0, min(255, int(max_brightness))))


# ── 通道分类（与引擎 :4242-4263 / 探针 classify_channels 同式）────────────

def classify_channels(fd_channels: Sequence, start: int, source: str):
    """def 通道 → (src_idx, rgbw{r,g,b,w})。start = start_channel-1。"""
    src_idx: List[int] = []
    rgbw: Dict[str, List[int]] = {"r": [], "g": [], "b": [], "w": []}
    for offset, ch_value in enumerate(fd_channels):
        idx = start + offset
        if idx < 0 or idx >= _UNI:
            continue
        ch_norm = str(getattr(ch_value, "value", ch_value)).strip().lower()
        if ch_norm == source:
            src_idx.append(idx)
        elif ch_norm in ("r", "red"):
            rgbw["r"].append(idx)
        elif ch_norm in ("g", "green"):
            rgbw["g"].append(idx)
        elif ch_norm in ("b", "blue"):
            rgbw["b"].append(idx)
        elif ch_norm in ("w", "white"):
            rgbw["w"].append(idx)
    return src_idx, rgbw


# ── scope 装配（= 探针 build_entries_and_cross/build_scope_blob, 逐字节）──

def _package_light_channels(fixture, defs_by_id, target_mask):
    """包灯目标通道，保留设备定义顺序；不裁剪供查找用的泵源设备。"""
    start = int(fixture.start_channel) - 1
    definition = defs_by_id.get(str(fixture.def_id))
    channels = list(definition.channels) if definition is not None else []
    return [
        start + offset for offset, channel in enumerate(channels)
        if 0 <= start + offset < _UNI
        and str(getattr(channel, "value", channel)).strip().lower()
        in ("r", "red", "g", "green", "b", "blue", "w", "white")
        and (target_mask is None or target_mask[start + offset])
    ]


def build_entries_and_cross(fixtures, defs_by_id, pkg_map, universe, source,
                            state_slots, color_slots, pkg_plane, fx_by_id=None,
                            target_mask=None):
    """scope 过滤(变更E) + 合并表(project 序) + 跨域索引序。

    entries: kind 0=主(源+RGBW)/1=同域泵 F5(源+包灯); cross_entries 索引序
    (pkg_map 遍历序)。state_slots/color_slots 为引擎级注册表（跨 scope 共享）。
    fx_by_id: 可选 {str(fi.id): fi} 查找表 — 生产路径传入避免 O(n²) 线性扫
    （万灯工程逐 scope 构建耗时）。默认 None → 内部构建一次（行为不变）。
    target_mask: 本 clip 的目标灯通道；None 保留旧探针直调语义。
    生产 build_lt_scope 总是传 light_mask，空目标在注册共享状态前跳过。
    """
    if target_mask is not None:
        target_mask = np.asarray(target_mask, dtype=bool)
        if target_mask.shape != (_UNI,):
            raise ValueError("light-tracking target mask must have 512 channels")
        if not target_mask.any():
            return [], []
    if fx_by_id is None:
        fx_by_id = {str(fi.id): fi for fi in fixtures}
    entries: List[dict] = []
    for fi in fixtures:
        if int(fi.universe) != int(universe):
            continue
        start = int(fi.start_channel) - 1
        fd = defs_by_id.get(str(fi.def_id))
        channels = list(fd.channels) if fd is not None else []
        src_idx, rgbw = classify_channels(channels, start, source)
        if not src_idx:
            continue
        fid = str(fi.id)
        if any(len(v) > 0 for v in rgbw.values()):
            if target_mask is not None:
                rgbw = {color: [idx for idx in indices if target_mask[idx]]
                        for color, indices in rgbw.items()}
                if not any(rgbw.values()):
                    continue
            source_key = "lt:%d:%s:%s" % (int(universe), fid, source)
            level_key = "lt:%d:%s:level:%s" % (int(universe), fid, source)
            color_key = "lt:%d:%s:color" % (int(universe), fid)
            so = state_slots.setdefault(source_key, len(state_slots))
            lo = state_slots.setdefault(level_key, len(state_slots))
            co = color_slots.setdefault(color_key, len(color_slots))
            entries.append({"kind": 0, "fid": fid, "src": src_idx,
                            "r": rgbw["r"], "g": rgbw["g"], "b": rgbw["b"],
                            "w": rgbw["w"], "state_off": so, "level_off": lo,
                            "color_off": co, "pkg": []})
        elif pkg_map.get(fid):
            pkg_ch: List[int] = []
            for lfid in pkg_map[fid]:
                lobj = fx_by_id.get(lfid)
                if lobj is None or int(lobj.universe) != int(universe):
                    continue
                pkg_ch.extend(_package_light_channels(lobj, defs_by_id, target_mask))
            if target_mask is not None and not pkg_ch:
                continue
            source_key = "lt:%d:%s:%s" % (int(universe), fid, source)
            so = state_slots.setdefault(source_key, len(state_slots))
            e = {"kind": 1, "fid": fid, "src": src_idx,
                 "r": [], "g": [], "b": [], "w": [],
                 "state_off": so, "level_off": -1, "color_off": -1,
                 "pkg": pkg_ch}
            e["pkg_off"] = len(pkg_plane) if pkg_ch else -1
            if pkg_ch:
                pkg_plane.extend(pkg_ch)
            entries.append(e)
    # 跨域索引: {light_uni: [(src_fid, src_uni)...]} 构建序 = pkg_map 遍历序
    by_uni2: Dict[int, List[Tuple[str, int]]] = {}
    for src_fid2, lights2 in pkg_map.items():
        sf = fx_by_id.get(src_fid2)
        if sf is None:
            continue
        su = int(sf.universe)
        for lfid2 in lights2:
            lo2 = fx_by_id.get(lfid2)
            if lo2 is None:
                continue
            lu = int(lo2.universe)
            if lu == su:
                continue
            if target_mask is not None and (
                    lu != int(universe)
                    or not _package_light_channels(lo2, defs_by_id, target_mask)):
                continue
            by_uni2.setdefault(lu, []).append((src_fid2, su))
    cross_entries: List[dict] = []
    for (src_fid2, suni2) in by_uni2.get(int(universe), []):
        sf2 = fx_by_id.get(src_fid2)
        if sf2 is None:
            continue
        sstart = int(sf2.start_channel) - 1
        sfd = defs_by_id.get(str(sf2.def_id))
        sch = list(sfd.channels) if sfd is not None else []
        src_idx2: List[int] = []
        for _off, _ct in enumerate(sch):
            _i = sstart + _off
            if 0 <= _i < _UNI and str(getattr(_ct, "value", _ct)).strip().lower() == source:
                src_idx2.append(_i)
        if not src_idx2:
            continue
        pkg_ch2: List[int] = []
        for lfid3 in (pkg_map.get(src_fid2) or []):
            lo3 = fx_by_id.get(lfid3)
            if lo3 is None or int(lo3.universe) != int(universe):
                continue
            pkg_ch2.extend(_package_light_channels(lo3, defs_by_id, target_mask))
        if target_mask is not None and not pkg_ch2:
            continue
        key2 = "lt:%d:%s:%s" % (suni2, src_fid2, source)
        so2 = state_slots.setdefault(key2, len(state_slots))
        e2 = {"fid": src_fid2, "pump_uni": suni2, "src": src_idx2,
              "state_off": so2, "pkg": pkg_ch2}
        e2["pkg_off"] = len(pkg_plane) if pkg_ch2 else -1
        if pkg_ch2:
            pkg_plane.extend(pkg_ch2)
        cross_entries.append(e2)
    return entries, cross_entries


def build_scope_blob(entries, cross_entries, pkg_plane, light_mask,
                     src_active_mask, clip_unis, threshold, attack, release,
                     color_is_white, cr, cg, cb, max_brightness=255) -> np.ndarray:
    """扁平块（布局见 lt_c_spec / _lt_scope_parse; 头 1168B + fx 行 + cx 行
    + pkg 平面）。uint8 C 连续。"""
    n_fx = sum(1 for e in entries if e["kind"] == 0)
    n_pumps = len(entries) - n_fx
    n_cross = len(cross_entries)
    n_pkg = len(pkg_plane)
    if len(clip_unis) > 16:
        raise ValueError("clip_universes > 16")
    hdr = np.zeros(_HDR, dtype=np.uint8)
    # ★ 2026-08-11: 头 10×int32（+max_brightness 开强度上限, C 侧同序解析）
    hdr[0:40] = np.asarray([n_fx, n_pumps, n_cross, len(clip_unis), threshold,
                            int(color_is_white), cr, cg, cb, max_brightness],
                           dtype=np.int32).view(np.uint8)
    hdr[40:48] = np.asarray([attack], dtype=np.float64).view(np.uint8)
    hdr[48:56] = np.asarray([release], dtype=np.float64).view(np.uint8)
    hdr[56:568] = np.asarray(light_mask, dtype=np.uint8).view(np.uint8)
    hdr[568:1080] = np.asarray(src_active_mask, dtype=np.uint8).view(np.uint8)
    cu = np.zeros(16, dtype=np.int32)
    cu[:len(clip_unis)] = clip_unis
    hdr[1080:1144] = cu.view(np.uint8)
    fx = np.zeros((len(entries), _FX_ROW), dtype=np.int32)
    for i, e in enumerate(entries):
        fx[i, 0] = e["kind"]
        fx[i, 1] = e["state_off"]
        fx[i, 2] = e.get("level_off", -1)
        fx[i, 3] = e.get("color_off", -1)
        fx[i, 4] = len(e["src"])
        fx[i, 5] = len(e["r"]); fx[i, 6] = len(e["g"])
        fx[i, 7] = len(e["b"]); fx[i, 8] = len(e["w"])
        fx[i, 9] = len(e["pkg"])
        fx[i, 10] = e.get("pkg_off", -1)
        fx[i, 11:11 + len(e["src"])] = e["src"]
        fx[i, 27:27 + len(e["r"])] = e["r"]
        fx[i, 43:43 + len(e["g"])] = e["g"]
        fx[i, 59:59 + len(e["b"])] = e["b"]
        fx[i, 75:75 + len(e["w"])] = e["w"]
    cx = np.zeros((n_cross, _CX_ROW), dtype=np.int32)
    for i, e in enumerate(cross_entries):
        cx[i, 0] = e["state_off"]
        cx[i, 1] = e["pump_uni"]
        cx[i, 2] = len(e["src"])
        cx[i, 3] = len(e["pkg"])
        cx[i, 4] = e.get("pkg_off", -1)
        cx[i, 5:5 + len(e["src"])] = e["src"]
    pkg = np.asarray(pkg_plane, dtype=np.int32)
    off_fx = _HDR
    off_cross = off_fx + len(entries) * _FX_ROW * 4
    off_pkg = off_cross + n_cross * _CX_ROW * 4
    hdr[1144:1148] = np.asarray([off_fx], dtype=np.int32).view(np.uint8)
    hdr[1148:1152] = np.asarray([off_fx], dtype=np.int32).view(np.uint8)
    hdr[1152:1156] = np.asarray([off_cross], dtype=np.int32).view(np.uint8)
    hdr[1156:1160] = np.asarray([off_pkg], dtype=np.int32).view(np.uint8)
    hdr[1160:1164] = np.asarray([n_pkg], dtype=np.int32).view(np.uint8)
    blob = np.frombuffer(hdr.tobytes() + fx.tobytes() + cx.tobytes() + pkg.tobytes(),
                         dtype=np.uint8)
    return np.ascontiguousarray(blob, dtype=np.uint8)


class LtScope:
    """(clip_key, universe) 编译产物: 扁平块 + 行描述（NM 采样/泵表用）。"""

    __slots__ = ("clip_key", "universe", "blob", "rows", "cross_rows",
                 "n_row", "n_cross", "color_is_white", "empty")

    def __init__(self, clip_key, universe, blob, rows, cross_rows,
                 color_is_white):
        self.clip_key = str(clip_key)
        self.universe = int(universe)
        self.blob = blob                      # np.uint8 C 连续
        self.rows = rows                      # entries（kind/fid/r/g/b/w/color_off）
        self.cross_rows = cross_rows          # cross_entries（pump_uni/src/state_off）
        self.n_row = len(rows)
        self.n_cross = len(cross_rows)
        self.color_is_white = bool(color_is_white)
        n_fx = sum(1 for e in rows if e["kind"] == 0)
        self.empty = (n_fx + (len(rows) - n_fx) + len(cross_rows)) == 0


def build_lt_scope(clip_key, universe, fixtures, defs_by_id, pkg_map,
                   source, light_mask, src_active_mask, clip_unis, params,
                   state_slots, color_slots, fx_by_id=None) -> Optional[LtScope]:
    """编译期 scope（每次结构变化重建; 结构签名由调用方管理）。

    light_mask/src_active_mask: 调用方按 clip 语义计算
    （= _clip_light_tracking_active_mask / _light_tracking_active_source_mask）。
    空 scope（无任何 LT 行）返回 None → 调用方标记任务非 LT（普通路径, 字节同）。
    fx_by_id: 可选查找表（万灯工程 O(n²) 规避）, 见 build_entries_and_cross。
    """
    pkg_plane: List[int] = []
    entries, cross_entries = build_entries_and_cross(
        fixtures, defs_by_id, pkg_map, int(universe), source,
        state_slots, color_slots, pkg_plane, fx_by_id, target_mask=light_mask)
    if not entries and not cross_entries:
        return None
    blob = build_scope_blob(entries, cross_entries, pkg_plane,
                            light_mask, src_active_mask, clip_unis,
                            int(params.threshold), float(params.attack),
                            float(params.release), params.color_is_white,
                            params.cr, params.cg, params.cb,
                            int(params.max_brightness))
    return LtScope(clip_key, int(universe), blob, entries, cross_entries,
                   params.color_is_white)


# ── 泵帧表（lt_pump_tbl_t: 原始长度 rows/lens）───────────────────────────

class PumpTbl(ctypes.Structure):
    _fields_ = [("n", ctypes.c_int),
                ("times", _PC_D),
                ("rows", ctypes.POINTER(_PC_U8)),
                ("lens", _PC_I)]


def make_pump_tbl(frames: Sequence) -> Tuple[PumpTbl, list]:
    """frames: Frame-like(timestamp, data)（升序）→ PumpTbl + keep(保活)。

    原始长度（不填充 512）写入 lens — C 侧 size 守卫按原始长度判定。
    """
    frames = sorted(frames, key=lambda f: float(getattr(f, "timestamp", 0.0) or 0.0))
    n = len(frames)
    times = np.asarray([float(getattr(f, "timestamp", 0.0) or 0.0)
                        for f in frames], dtype=np.float64)
    lens = np.asarray([int(np.asarray(f.data).size) for f in frames],
                      dtype=np.int32)
    row_arr = (ctypes.c_void_p * max(1, n))()
    keep = []
    for i, f in enumerate(frames):
        buf = ctypes.create_string_buffer(bytes(np.asarray(f.data, dtype=np.uint8).tobytes()))
        keep.append(buf)
        row_arr[i] = ctypes.cast(buf, ctypes.c_void_p)
    tbl = PumpTbl()
    tbl.n = n
    tbl.times = times.ctypes.data_as(_PC_D) if n else None
    tbl.rows = ctypes.cast(row_arr, ctypes.POINTER(_PC_U8)) if n else None
    tbl.lens = lens.ctypes.data_as(_PC_I) if n else None
    keep.extend([times, lens, row_arr])
    return tbl, keep


# ── 引擎级状态槽（跨 clip/跨帧持久, 触达回写 dict）───────────────────────

class LtEngineState:
    """引擎级 LT 状态: 槽数组 + 键表。seed_from 装入（dict → 槽, 含 exists）;
    back_write 按 touched/touched_color 增量回写 dict（分支3 不写 time —
    C 不置 touched → 回写自动跳过, 与 Python 语义一致）。"""

    __slots__ = ("n_state", "n_color", "state", "state_t", "state_exists",
                 "color", "color_valid", "touched", "touched_color",
                 "state_keys", "color_keys")

    def __init__(self, state_slots: Dict[str, int], color_slots: Dict[str, int]):
        self.n_state = len(state_slots)
        self.n_color = len(color_slots)
        self.state = np.zeros(self.n_state, dtype=np.float64)
        self.state_t = np.zeros(self.n_state, dtype=np.float64)
        self.state_exists = np.zeros(self.n_state, dtype=np.uint8)
        self.color = np.zeros(3 * self.n_color, dtype=np.uint8)
        self.color_valid = np.zeros(self.n_color, dtype=np.uint8)
        self.touched = np.zeros(self.n_state, dtype=np.uint8)
        self.touched_color = np.zeros(self.n_color, dtype=np.uint8)
        self.state_keys = [""] * self.n_state
        for key, off in state_slots.items():
            self.state_keys[off] = key
        self.color_keys = [""] * self.n_color
        for key, off in color_slots.items():
            self.color_keys[off] = key

    def seed_from(self, state_dict, stime_dict, color_dict) -> None:
        """挂载/重建时装入（Python 路径既有状态 → 槽）。

        ★ R173: 原写 `for key, off in enumerate(...)`（解包反了 → key 是下标, 永不
        命中 → 装入恒为空操作）: 槽容量增长重建（新 LT clip 注册新键）时 C 丢光
        全部既有状态与颜色, Python 字典却保留 → C/Python 分叉。"""
        for off, key in enumerate(self.state_keys):
            if key and key in state_dict:
                self.state[off] = float(state_dict[key])
                self.state_t[off] = float(stime_dict[key])
                self.state_exists[off] = 1
        for off, key in enumerate(self.color_keys):
            if key and key in color_dict:
                self.color[off * 3:off * 3 + 3] = color_dict[key]
                self.color_valid[off] = 1

    def clear_state(self) -> None:
        """LT 断档（R173, src/engine/light_tracking_scope）: 就地清状态槽, 颜色保留
        （= Python 清 state/time 字典、留 color_cache）。touched 同清 —— 它是"自上次
        清空以来写过"的累计标记, 不清则 back_write 会把 0 写回字典造出假键。
        就地清 → K2 束与实时 hook 共用的同一槽对象不分叉。"""
        self.state[:] = 0.0
        self.state_t[:] = 0.0
        self.state_exists[:] = 0
        self.touched[:] = 0

    def back_write(self, state_dict, stime_dict, color_dict) -> None:
        """帧后 touched 增量回写（只写本帧真正更新的槽）。

        ★ 2026-08-11: 原实现每帧遍历全部槽检查 touched（示例工程 n_state 大,
        0.147ms/帧 × 7149 ≈ 1s 纯遍历）; np.nonzero 只迭代脏槽, dict 写
        key 唯一 → 迭代顺序无影响, 语义逐字节等价。
        """
        for off in np.nonzero(self.touched)[0]:
            key = self.state_keys[off]
            if key:
                state_dict[key] = float(self.state[off])
                stime_dict[key] = float(self.state_t[off])
        for off in np.nonzero(self.touched_color)[0]:
            key = self.color_keys[off]
            if key:
                color_dict[key] = (int(self.color[off * 3]),
                                   int(self.color[off * 3 + 1]),
                                   int(self.color[off * 3 + 2]))


def lt_state_needs_rebuild(st, n_state: int, n_color: int,
                           state_dict, stime_dict, color_dict) -> bool:
    """引擎级槽对象是否需重建+装入（K2 构建与实时 hook 共用判据）。

    结构/容量增长 → 重建。state/time 字典被清空时: 槽里仍有旧状态（invalidate_all_caches
    整清）或颜色缓存已清而槽里还有颜色 → 重建; R173 LT 断档已就地 clear_state（槽空、
    颜色与缓存一致）→ 续用同一对象（换对象会让 K2 束持有的旧对象与实时 hook 分叉）。"""
    if st is None or int(st.n_state) < n_state or int(st.n_color) < n_color:
        return True
    if n_state <= 0 or state_dict or stime_dict:
        return False
    return bool(st.state_exists.any()) or (not color_dict and bool(st.color_valid.any()))


# ── 每帧束（ce_timeline_frame 尾段 16 参 + Python 侧保活）────────────────

class LtFrameBundle:
    """每帧 LT 束: 任务表 → scope_off[n_tasks] + 每任务指针数组。

    属性名与 TimelineCtx._lt_args 对齐（scope_off/scope_ptrs/nm_ptrs/
    fx_ptrs/cross_tbl_ptrs/cross_t0_ptrs/cross_live_ptrs + 引擎状态数组 +
    n_state/n_color）。_keep 保活全部 ctypes/numpy 缓冲（C 调用期间防 GC）。
    """

    __slots__ = ("scope_off", "scope_ptrs", "nm_ptrs", "fx_ptrs",
                 "cross_tbl_ptrs", "cross_t0_ptrs", "cross_live_ptrs",
                 "state", "state_t", "state_exists", "color", "color_valid",
                 "touched", "touched_color", "n_state", "n_color", "_keep")


def lt_scope_off_for_tasks(tasks: Sequence[dict],
                           scope_of: Callable[[dict], Optional[int]]) -> np.ndarray:
    """[n_tasks] int32: task → scope 句柄索引（-1 = 非 LT, 普通路径）。

    tasks: K2 任务元组同源（含 clip_key/universe/start_t）; scope_of 返回
    该任务 scope 索引或 None。空任务表 → 空数组（C 侧 has_lt 仍走, 循环跳过）。
    """
    arr = np.full(len(tasks), -1, dtype=np.int32)
    for i, task in enumerate(tasks):
        idx = scope_of(task)
        if idx is not None:
            arr[i] = int(idx)
    return arr


def build_lt_bundle(tasks: Sequence[dict], scopes: Dict[Tuple[str, int], LtScope],
                    state: LtEngineState, pos: float, live_fn, nm_fn,
                    clip_frames_for: Callable[[str, int], Sequence],
                    alt_frames_for: Callable[[int, str, float], Optional[Tuple[Sequence, float]]],
                    ) -> LtFrameBundle:
    """每帧装配: 逐任务构建 nm_rgb/fx_live/cross 数组 + scope 句柄数组。

    tasks: 每帧任务序（含 clip_key/universe/start_t; 序 = C 表序, 与 Python
    参考逐任务语义一致）。pos = 帧时间线位置; local_t = pos - start_t[t]
    （= C 侧同式, 供跨域帧选择时间基）。live_fn(uni, exclude_clip=True) /
    nm_fn(uni) → u8 数组或 None; clip_frames_for(clip_key, uni) → 本 clip
    帧列表; alt_frames_for(pump_uni, clip_key, pos) → (帧列表, 泵clip局部
    时间) 或 None（= _lt_find_pump_frames 语义: 其他活跃 clip, 自身 start
    对齐）。返回束; 无 LT 任务时 scope_off 全 -1（调用方可用 None 短路）。
    """
    n_tasks = len(tasks)
    scope_ptrs = (ctypes.c_void_p * n_tasks)()
    scope_idx_of: Dict[Tuple[str, int], int] = {}

    def scope_of(task: dict) -> Optional[int]:
        key = (str(task["clip_key"]), int(task["universe"]))
        sc = scopes.get(key)
        if sc is None or sc.empty:
            return None
        if key not in scope_idx_of:
            scope_idx_of[key] = len(scope_idx_of)
            scope_ptrs[scope_idx_of[key]] = ctypes.cast(
                sc.blob.ctypes.data_as(_PC_U8), ctypes.c_void_p)
        return scope_idx_of[key]

    scope_off = lt_scope_off_for_tasks(tasks, scope_of)
    nm_ptrs = (ctypes.c_void_p * n_tasks)()
    fx_ptrs = (ctypes.c_void_p * n_tasks)()
    cross_tbl_ptrs = (ctypes.c_void_p * n_tasks)()
    cross_t0_ptrs = (ctypes.c_void_p * n_tasks)()
    cross_live_ptrs = (ctypes.c_void_p * n_tasks)()
    keep: List = []
    for ti, task in enumerate(tasks):
        if scope_off[ti] < 0:
            continue
        clip_key = str(task["clip_key"])
        uni = int(task["universe"])
        scope = scopes[(clip_key, uni)]
        local_t = float(pos) - float(task["start_t"])
        nm, fx = _fill_scope_arrays(scope, uni, state, live_fn, nm_fn)
        keep.append(nm)
        keep.append(fx)
        nm_ptrs[ti] = ctypes.cast(nm.ctypes.data_as(_PC_U8), ctypes.c_void_p)
        fx_ptrs[ti] = ctypes.cast(fx.ctypes.data_as(_PC_I), ctypes.c_void_p)
        if scope.n_cross > 0:
            tbl, t0, live, kb = _build_cross_arrays(
                scope, clip_key, uni, local_t, pos, live_fn,
                clip_frames_for, alt_frames_for)
            keep.extend(kb)
            cross_tbl_ptrs[ti] = ctypes.cast(tbl, ctypes.c_void_p)
            cross_t0_ptrs[ti] = t0
            cross_live_ptrs[ti] = live
    bundle = LtFrameBundle()
    bundle.scope_off = scope_off
    bundle.scope_ptrs = scope_ptrs
    bundle.nm_ptrs = nm_ptrs
    bundle.fx_ptrs = fx_ptrs
    bundle.cross_tbl_ptrs = cross_tbl_ptrs
    bundle.cross_t0_ptrs = cross_t0_ptrs
    bundle.cross_live_ptrs = cross_live_ptrs
    bundle.state = state.state
    bundle.state_t = state.state_t
    bundle.state_exists = state.state_exists
    bundle.color = state.color
    bundle.color_valid = state.color_valid
    bundle.touched = state.touched
    bundle.touched_color = state.touched_color
    bundle.n_state = state.n_state
    bundle.n_color = state.n_color
    bundle._keep = keep
    return bundle


def _fill_scope_arrays(scope: LtScope, universe: int, state: LtEngineState,
                       live_fn, nm_fn) -> Tuple[np.ndarray, np.ndarray]:
    """每任务 nm_rgb[3*n_row] + fx_live[n_row]（C 语义: nm 采样只喂无效槽,
    C 内核按 raw 写 color_valid 决定取舍 — 无条件采样无害）。"""
    n_row = scope.n_row
    fx_live = np.zeros(n_row, dtype=np.int32)
    nm_rgb = np.zeros(3 * n_row, dtype=np.uint8)
    for i, e in enumerate(scope.rows):
        if e["kind"] == 1:
            src_idx = e["src"]
            if live_fn is not None:
                live = live_fn(int(universe), exclude_clip=True)
                if live is not None:
                    arr = np.asarray(live, dtype=np.uint8)
                    from src.engine.native_ownership import pump_level
                    fx_live[i] = pump_level(arr, src_idx)
        elif scope.color_is_white and e["color_off"] >= 0:
            off = int(e["color_off"])
            if not state.color_valid[off]:
                r = e["r"]; g = e["g"]; b = e["b"]; w = e["w"]
                nm_out = nm_fn(int(universe)) if nm_fn is not None else None
                from src.engine.native_ownership import color_source
                sr, sg, sb = color_source(nm_out, [row[0] if row else -1 for row in (r, g, b, w)])
                nm_rgb[i * 3] = sr
                nm_rgb[i * 3 + 1] = sg
                nm_rgb[i * 3 + 2] = sb
    return nm_rgb, fx_live


def _build_cross_arrays(scope: LtScope, clip_key: str, universe: int,
                        local_t: float, pos: float, live_fn,
                        clip_frames_for, alt_frames_for):
    """跨域泵段数组: 泵帧表(本 clip 帧 > alt 帧) + t0 + live 兜底。

    local_t = 跨域帧选择默认时间基（本 clip 帧表）; pos = 时间线位置
    （= _lt_find_pump_frames 的 _now, 供 alt clip start 对齐）。
    返回 (tbl_ptrs c_void_p*, t0_ptrs c_void_p*, live_ptrs c_void_p*, keep)。
    """
    n_cross = scope.n_cross
    tbl_ptrs = (ctypes.c_void_p * n_cross)()
    t0 = np.zeros(n_cross, dtype=np.float64)
    live = np.zeros(n_cross, dtype=np.int32)
    keep: List = []
    for ei, ce in enumerate(scope.cross_rows):
        suni = int(ce["pump_uni"])
        pframes = list(clip_frames_for(str(clip_key), suni) or [])
        t0v = local_t
        if not pframes:
            alt = alt_frames_for(suni, str(clip_key), pos)
            if alt is not None:
                pframes, t0v = alt
        if pframes:
            tbl, kb = make_pump_tbl(pframes)
            keep.extend(kb)
            keep.append(tbl)
            tbl_ptrs[ei] = ctypes.cast(ctypes.pointer(tbl), ctypes.c_void_p)
        t0[ei] = t0v
        if live_fn is not None and ce["src"]:
            lv = live_fn(suni, exclude_clip=True)
            if lv is not None:
                arr = np.asarray(lv, dtype=np.uint8)
                from src.engine.native_ownership import pump_level
                live[ei] = pump_level(arr, ce["src"])
    keep.extend([t0, live])
    return (tbl_ptrs,
            ctypes.cast(t0.ctypes.data_as(_PC_D), ctypes.c_void_p),
            ctypes.cast(live.ctypes.data_as(_PC_I), ctypes.c_void_p),
            keep)
