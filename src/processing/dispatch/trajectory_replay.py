# -*- coding: utf-8 -*-
"""控件轨迹回放（纯数据，无 Qt）—— 把 clip.trajectories 展开成 DMX 叠加帧。

语义（设计 v2.1）：录制=控件函数轨迹；回放=按 clip 时长**周期循环**的叠加调制，
写入独立 SHAPE 层 `clip_traj`（叠加语义由层混合获得）；released 键查表跳过。

本模块只做**纯计算**：
  输入：clip 的轨迹/设备顺序/通道映射上下文 + 源时间 source_t
  输出：{universe: {dmx_offset_0based: value}} 的**增量**（调用方合并到层帧）
引擎侧是薄调用者（单写者：多 clip 合并后一次 update_layer，v2.1 §5.4）。

回放器注册表 `CONTROL_APPLIERS`：每 control_kind 一个 applier（可扩展点，
不写 if/else 蔓延）。当前实现两类通道可寻址轨迹：
  programmer|strip|<ct>   值=int DMX → 该 ct 全部偏移
  programmer|range|<ct>   值=(mode,base,target) → 沿设备顺序插值
控制域轨迹（joystick/joyamp/zsync/effect|param/global_speed）：录制的**输出效果**
已在 clip frames 里；其"控件层叠加"属下一增量（applier 注册 None 并注明）。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from .native_trajectory_replay import apply_range, build, domain, sample

# control_key 前缀 → applier
# applier(ctx, key_suffix, value, out) ：把该事件写进 out {uni: {off0: val}}
CONTROL_APPLIERS: Dict[str, Callable] = {}


def _apply_strip(ctx, ct: str, value, out) -> None:
    native = domain()
    if native is not None and native.accepts_value(value, "strip", ctx):
        return native.apply(ctx, ct, value, out, "strip")
    try:
        v = max(0, min(255, int(round(float(value)))))
    except (TypeError, ValueError):
        return
    for uni, off in ctx.offsets_for(ct):
        out.setdefault(uni, {})[off] = v


def _apply_range(ctx, ct: str, value, out) -> None:
    return apply_range(ctx, ct, value, out)


def _apply_master(ctx, ct: str, value, out) -> None:
    """programmer|master|<ct>：主控值（0-255）→ 该 ct 全部设备同值。

    与 strip 同址（master 语义 = 全设备同值）；区别在录制来源：master 键由
    轨迹页 read 模式回放写回面板主值链时发出（automation_read_step），
    strip 键来自逐设备条带事件。值钳位/取整与 strip 一致。
    """
    native = domain()
    if native is not None and native.accepts_value(value, "master", ctx):
        return native.apply(ctx, ct, value, out, "master")
    _apply_strip(ctx, ct, value, out)


CONTROL_APPLIERS["programmer|strip"] = _apply_strip
CONTROL_APPLIERS["programmer|range"] = _apply_range
CONTROL_APPLIERS["programmer|master"] = _apply_master


def _build_channel_map(order, fixture_types, start_channels) -> Dict[str, Dict[str, List[int]]]:
    """构建 fixture_id → {ch_type: [dmx_offset_1based]} 映射（模块级：__init__ 基线）。

    ★ R193/P2-02（PB-03）：钳 512 上界——贴 universe 尾配接的宽设备生成 >511
    偏移，会让叠加层 fancy-index 越界、整帧叠加被丢弃。
    """
    by_fixture: Dict[str, Dict[str, List[int]]] = {}
    for fid in order:
        types = fixture_types.get(str(fid))
        if not types:
            continue
        start = int(start_channels.get(str(fid), 1) or 1)
        m: Dict[str, List[int]] = {}
        for i, ct in enumerate(types):
            off = start + i
            if 0 < off <= 512:
                m.setdefault(str(ct), []).append(off)
        by_fixture[str(fid)] = m
    return by_fixture


class ReplayContext:
    """一次回放的通道映射上下文（缓存构建，O(设备×通道) 一次）。"""

    def __init__(self, fixture_types: Dict[str, List[str]],
                 base_order_fixture_ids: List[str],
                 fixture_universes: Optional[Dict[str, int]] = None,
                 fixture_start_channels: Optional[Dict[str, int]] = None):
        # fixture_id → {ch_type: [dmx_offset_1based]}（与 Programmer 行同源映射）
        self._order: List[str] = [str(x) for x in (base_order_fixture_ids or [])]
        self._uni = dict(fixture_universes or {})
        self._start = dict(fixture_start_channels or {})
        # ★ R193/P2-02：映射构建抽模块函数（行数/CC 基线），内含 512 钳制
        self._by_fixture = _build_channel_map(
            self._order, fixture_types or {}, self._start)

    def offsets_for(self, ch_type: str):
        """[(universe, offset_0based), ...] 该通道类型全部设备（按设备顺序）。"""
        out = []
        for fid in self._order:
            m = self._by_fixture.get(fid)
            if not m:
                continue
            uni = int(self._uni.get(fid, 1) or 1)
            for off1 in m.get(str(ch_type), ()):  # 1-based
                out.append((uni, int(off1) - 1))
        return out

    def offsets_by_fixture(self, ch_type: str):
        """[(universe, offset_0based, 设备序 index), ...]（range 插值按设备序）。"""
        out = []
        for i, fid in enumerate(self._order):
            m = self._by_fixture.get(fid)
            if not m:
                continue
            uni = int(self._uni.get(fid, 1) or 1)
            for off1 in m.get(str(ch_type), ()):
                out.append((uni, int(off1) - 1, i))
        return out


def sample_trajectory(seq: List[Tuple[float, object]], t: float):
    """线性插值取 t 处的轨迹值（数值插值；非数值=阶梯取最近）。序列空→None。"""
    return sample(seq, t)


def build_clip_traj_frame(clip, source_t: float,
                          fixture_types: Dict[str, List[str]],
                          fixture_universes: Dict[str, int],
                          fixture_start_channels: Dict[str, int],
                          released: "frozenset[str]" = frozenset(),
                          ) -> Dict[int, Dict[int, int]]:
    """展开一个 clip 在 source_t 的轨迹叠加帧 → {universe: {offset_0based: value}}。

    - 周期：source_t 已由调用方按 clip 时长取模（本模块不管播放窗口）。
    - released：control_key（clip 内粒度）集合，命中跳过。
    """
    return build(clip, source_t, (fixture_types, fixture_universes,
                                  fixture_start_channels), released)
