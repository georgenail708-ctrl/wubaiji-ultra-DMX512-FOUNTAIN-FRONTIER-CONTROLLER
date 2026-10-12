"""
voice_clip_renderer — 声部决策 → 时间线 clip(L4)

把 L3 的每声部动作窗口(VoiceClipWindow)离线渲染成 models.Clip(含逐帧 DMXFrame)。
纯 core,无 Qt:用本包确定性原语(motor_path / shape_engine / topology)+ 直接地址映射
生成每帧每通道值,不碰 effect_browser 实时回放路径(零回归风险)。

三种机制:
  static  : shape_engine 冻结某 phase → 每台固定值,每帧保持(泵山形缓开保持)
  effect  : 动态。电机用 motor_path 走摇杆路径(幅度模式);泵/阀/RGB 用 shape 流动相位
  matrix  : 同 effect 的 RGB 流动(矩阵设备运行时由矩阵引擎细化,这里产 RGB 值序列)

确定性:虚拟时间 t=i/fps,无 time.monotonic;随机吃 seed。timestamp 0 基(相对 clip 起点)。
active_channels_by_universe 从"写过的地址"累积(含零值电机中位),防回放被 base_active 掩黑。
电机 16bit:有 fine 通道时用 coarse/fine 拆分,防慢速台阶顿挫。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import motor_path as _motor
from . import shape_engine as _shape
from . import topology as _topo

# R183 hsv 统一（本文件为 4 处定义之一）：与 command_arrange/renderer.py 同款
# 多前缀兜底（app 把 src 加进 path → 顶层 utils；包内 → 相对三层）。
try:
    from ...utils.color_math import hsv_to_rgb_u8_round_clamped as _hsv_shared
except ImportError:
    try:
        from utils.color_math import hsv_to_rgb_u8_round_clamped as _hsv_shared
    except ImportError:
        from src.utils.color_math import hsv_to_rgb_u8_round_clamped as _hsv_shared

# 用法 → 该声部要写的通道类型
USAGE_CH_TYPES = {
    "数控": ["motor_x", "motor_y"],
    "泵": ["pump"],
    "阀": ["valve"],
    "RGB": ["R", "G", "B"],
    "调光": ["dimmer"],
}
FPS_DEFAULT = 40


def _load_models():
    """跨包导入 Clip/DMXFrame:绝对导入+多前缀兜底(app把src加进path→顶层core)。"""
    try:
        from core.models import Clip, DMXFrame
    except ImportError:
        try:
            from src.core.models import Clip, DMXFrame
        except ImportError:
            from ...core.models import Clip, DMXFrame
    return Clip, DMXFrame


def _get_effect_engine():
    """惰性取真 EffectEngine 单例(避开 core→ui 方向硬依赖;失败返回 None)。"""
    try:
        from ui.effect_engine import get_engine
    except ImportError:
        try:
            from src.ui.effect_engine import get_engine
        except ImportError:
            return None
    try:
        return get_engine()
    except Exception:
        return None


class _FX:
    """喂给 EffectEngine.compute 的轻量 fixture(需 .id 与 .def_id)。"""
    __slots__ = ("id", "def_id")

    def __init__(self, fid, def_id=""):
        self.id = fid
        self.def_id = def_id


def _render_via_engine(decision, member_ids, patch, coords, usage, voice_class,
                       start_time, duration, fps, seed, frame_bufs, acts,
                       _ensure, _mark, n_frames, level, preset_id, subsets,
                       defs=None, def_of=None):
    """用真 EffectEngine.compute 渲染一个声部窗口为 Clip(解锁真 preset 库的丰富变化)。

    把 ef.values[orig_i][ch_type] 按 patch 地址映射回 universe buffer;电机轴走 16bit。
    确定性:compute 内 t=frame_idx/fps 纯函数。失败返回 None(调用方回退自制 shape)。
    """
    eng = _get_effect_engine()
    if eng is None or eng.get_preset(preset_id) is None:
        return None
    Clip, DMXFrame = _load_models()
    # 有序成员(拓扑序)作 base_order,sort 用 linear_x
    ordered = []
    for s in subsets:
        for fid in s:
            if fid not in ordered:
                ordered.append(fid)
    ordered = [f for f in ordered if f in patch] or [f for f in member_ids if f in patch]
    if not ordered:
        return None
    fixtures = [_FX(f, (def_of or {}).get(f, "")) for f in ordered]
    positions = {f: {"pos_x": coords.get(f, (0.0, 0.0))[0],
                     "pos_y": coords.get(f, (0.0, 0.0))[1]} for f in ordered}
    params = dict(decision.get("preset_params", {}) or {})
    # 幅度:用 level 作整体强度(若 preset 有 min/max 之类由其默认,这里仅注入通用键)
    params.setdefault("speed", max(0.05, min(5.0, float(decision.get("speed_dmx", 100.0)) / 120.0)))
    try:
        frames_eff = eng.compute(preset_id, params, fixtures, defs or {}, positions,
                                 duration=float(duration), fps=float(fps),
                                 sort_mode="linear_x", base_order_ids=ordered)
    except Exception:
        return None
    if not frames_eff:
        return None
    nf = min(n_frames, len(frames_eff))
    lvl = max(0.0, min(1.0, float(level)))
    for i in range(nf):
        ef = frames_eff[i]
        vals = getattr(ef, "values", None)
        if not vals:
            continue
        for orig_i, fid in enumerate(ordered):
            if orig_i >= len(vals):
                break
            chvals = vals[orig_i] or {}
            p = patch.get(fid)
            if not p:
                continue
            uni = p["universe"]; chm = p["ch"]
            buf = _ensure(frame_bufs[i], uni)
            for ct, raw in chvals.items():
                a = chm.get(ct)
                if a is None or not (0 <= a < 512):
                    continue
                v = max(0, min(255, int(float(raw or 0))))
                # 非高潮场景对 RGB/灯做 level 衰减(电机/位置类不缩放,保持路径)
                if ct in ("R", "G", "B", "dimmer", "pump", "valve"):
                    v = int(round(v * (0.4 + 0.6 * lvl)))
                if ct in ("motor_x", "motor_y") and (a + 0) < 512:
                    fine = chm.get(ct + "_fine")
                    if fine is not None and 0 <= fine < 512:
                        c, f = _logical_16(float(v)); buf[a] = c; buf[fine] = f; _mark(uni, fine)
                        _mark(uni, a)
                        continue
                buf[a] = max(0, min(255, v))
                _mark(uni, a)
    frames = []
    for i in range(nf):
        t = i / fps
        for uni, buf in frame_bufs[i].items():
            frames.append(DMXFrame(universe=uni, timestamp=t, data=buf.copy()))
    if not frames:
        return None
    clip_universes = sorted(acts.keys())
    acbu = {u: sorted(ch) for u, ch in acts.items()}
    clip = Clip(
        track_id="", start_time=float(start_time), duration=float(duration),
        frames=frames, label=f"AUTO {usage}·{preset_id[:16]}",
        clip_universes=clip_universes or [1],
        active_channels_by_universe=acbu,
        fade_in=float(decision.get("fade_in", 0.6) or 0.6),
        fade_out=float(decision.get("fade_out", 0.6) or 0.6),
        playback_rate=1.0, source_duration=float(duration), preset_name=preset_id)
    if bool(decision.get("use_sequence", False)):
        clip.fade_groups = 4
        clip.fade_group_sequence_mode = "sequential"
        clip.fade_direction = "ltr" if decision.get("direction") == "L2R" else "rtl"
        clip.fade_in_direction = clip.fade_direction
    return clip


def _build_fixture_defs(project: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """构建 (def_id→FixtureDefinition, instance_id→def_id) 双映射。

    与 build_patch 同口径:fixture_defs 是 {def_id: FixtureDefinition}(对齐
    deep_choreography_engine.setup_fixtures 约定),fixtures 是设备实例(.id/.def_id)。
    """
    defs: Dict[str, Any] = {}
    for fd in getattr(project, "fixture_defs", []) or []:
        did = getattr(fd, "id", None) or (fd.get("id") if isinstance(fd, dict) else None)
        if did is not None:
            defs[str(did)] = fd
    inst_def: Dict[str, str] = {}
    for fx in getattr(project, "fixtures", []) or []:
        fid = getattr(fx, "id", None) or (fx.get("id") if isinstance(fx, dict) else None)
        did = (getattr(fx, "def_id", None) or getattr(fx, "fixture_def_id", None)
               or (fx.get("def_id") if isinstance(fx, dict) else None))
        if fid is not None and did is not None:
            inst_def[str(fid)] = str(did)
    return defs, inst_def


def build_patch(project: Any) -> Dict[str, Dict[str, Any]]:
    """从项目构建 {fid: {'universe':u, 'ch':{ch_type: addr0(0-based), ...}}}。"""
    defs = {}
    for fd in getattr(project, "fixture_defs", []) or []:
        did = getattr(fd, "id", None) or (fd.get("id") if isinstance(fd, dict) else None)
        chs = getattr(fd, "channels", None)
        if chs is None and isinstance(fd, dict):
            chs = fd.get("channels", [])
        types = [str(c.get("type") if isinstance(c, dict) else c) for c in (chs or [])]
        if did is not None:
            defs[did] = types
    patch: Dict[str, Dict[str, Any]] = {}
    for fx in getattr(project, "fixtures", []) or []:
        fid = getattr(fx, "id", None) or (fx.get("id") if isinstance(fx, dict) else None)
        did = (getattr(fx, "def_id", None) or getattr(fx, "fixture_def_id", None)
               or (fx.get("def_id") if isinstance(fx, dict) else None))
        if fid is None or did not in defs:
            continue
        uni = int(getattr(fx, "universe", None) or (fx.get("universe", 1) if isinstance(fx, dict) else 1) or 1)
        base = int(getattr(fx, "start_channel", None) or (fx.get("start_channel", 1) if isinstance(fx, dict) else 1) or 1)
        chmap = {}
        for off, ct in enumerate(defs[did]):
            chmap[ct] = base + off - 1   # 0-based
        patch[fid] = {"universe": uni, "ch": chmap}
    return patch


def _ordered_subsets(member_ids: Sequence[str], coords: Dict[str, Tuple[float, float]],
                     topo_kind: str, direction: str) -> List[List[str]]:
    """用 topology 把声部设备按拓扑子选成有序子集(可多组,如奇偶呼应)。"""
    try:
        subs = _topo.select_subset(list(member_ids), coords, topo_kind)
    except Exception:
        subs = [list(member_ids)]
    if not subs:
        subs = [list(member_ids)]
    out = []
    for s in subs:
        try:
            out.append(_topo.direction_order(s, direction, coords))
        except Exception:
            out.append(list(s))
    return out


def _speed_to_cycles(speed_dmx: float, duration: float) -> float:
    """把目标速度 DMX/s 折成整段内的波动周期数(用于流动相位)。
    一个正弦 0..255 单周期峰峰变化 ~510;速度越高周期越多。保守取值。"""
    if duration <= 0:
        return 1.0
    total_change = max(1.0, speed_dmx) * duration
    cycles = total_change / 510.0
    return max(0.25, min(cycles, duration * 4.0))   # 上限避免过快


def render_voice_window(
    decision: dict,
    member_ids: Sequence[str],
    patch: Dict[str, Dict[str, Any]],
    coords: Dict[str, Tuple[float, float]],
    usage: str,
    voice_class: str,
    start_time: float,
    duration: float,
    fps: int = FPS_DEFAULT,
    seed: int = 0,
    defs: Optional[Dict[str, Any]] = None,
    def_of: Optional[Dict[str, str]] = None,
) -> Optional[Any]:
    """渲染一个声部动作窗口为 models.Clip。member_ids=该声部全部设备。"""
    ch_types = USAGE_CH_TYPES.get(usage, [])
    if not ch_types or duration <= 0 or not member_ids:
        return None
    Clip, DMXFrame = _load_models()
    mechanism = str(decision.get("mechanism", "effect"))
    shape = str(decision.get("shape", "flat"))
    topo_kind = str(decision.get("topo_kind", "whole"))
    direction = str(decision.get("direction", "L2R"))
    level = float(decision.get("level", 1.0) or 1.0)
    speed_dmx = float(decision.get("speed_dmx", 100.0) or 100.0)
    energy = max(0.0, min(1.0, level))
    n_frames = max(1, int(round(duration * fps)) + 1)

    # 拓扑子选 → 有序子集(奇偶=两组,给相反相位呼应)
    subsets = _ordered_subsets(member_ids, coords, topo_kind, direction)
    active_set = set()
    for s in subsets:
        active_set.update(s)

    # 累积写过的地址(1-based) per universe
    acts: Dict[int, set] = {}
    # 逐帧 buffer: {universe: np.uint8[512]} list
    frame_bufs: List[Dict[int, np.ndarray]] = [dict() for _ in range(n_frames)]

    def _ensure(buf_map, uni):
        b = buf_map.get(uni)
        if b is None:
            b = np.zeros(512, dtype=np.uint8)
            buf_map[uni] = b
        return b

    def _mark(uni, addr0):
        acts.setdefault(uni, set()).add(addr0 + 1)   # 1-based

    # ★ real_effect 分支:动态非矩阵声部调真 EffectEngine.compute 渲染(解锁1611真preset)。
    #   只有 decision 给了 preset_id 且 mechanism=='real_effect' 才走;否则落原 shape/motor 路径。
    preset_id = str(decision.get("preset_id", "") or "")
    if mechanism == "real_effect" and preset_id and usage != "数控":
        eng_clip = _render_via_engine(
            decision, member_ids, patch, coords, usage, voice_class,
            start_time, duration, fps, seed, frame_bufs, acts, _ensure, _mark,
            n_frames, level, preset_id, subsets, defs, def_of)
        if eng_clip is not None:
            return eng_clip
        # 引擎渲染失败 → 继续走下面的自制 shape 兜底

    if usage == "数控":
        # 电机:motor_path 生成虚拟摇杆路径(幅度模式),全声部共用虚拟路径
        path_type = shape if shape in ("circle", "arc", "lissajous", "breath", "custom") else \
            ("circle" if energy >= 0.5 else "breath")
        spd = max(0.3, min(3.0, speed_dmx / 100.0))
        mp = _motor.gen_motor_path(path_type, duration, fps=fps, speed=spd, energy=energy,
                                   seed=seed, amplitude_mode=True,
                                   motor_transition=bool(decision.get("use_motor_phase", False)))
        xs = mp["x"]; ys = mp["y"]
        nf = min(n_frames, len(xs))
        # ★ per-fixture 相位铺开按**空间结构**(非拓扑序号):指向像涟漪在阵列内传播,
        #   层层绽放而非整片僵硬同步转。两种空间相位:
        #   - 环形布局 → 按半径(内→外)铺开,涟漪从中心荡到边缘;
        #   - 线形布局 → 按主轴位置铺开,波浪沿排扫过。
        ordered_m = []
        for s in subsets:
            for fid in s:
                if fid not in ordered_m:
                    ordered_m.append(fid)
        # 计算每台设备的空间相位坐标(0..1):优先半径(环),回退主轴投影/序号
        mcoords = {fid: coords.get(fid) for fid in ordered_m if coords.get(fid)}
        spatial_phase: Dict[str, float] = {}
        if len(mcoords) >= 3:
            mxs = [c[0] for c in mcoords.values()]; mys = [c[1] for c in mcoords.values()]
            ccx = sum(mxs) / len(mxs); ccy = sum(mys) / len(mys)
            rads = {fid: math.hypot(c[0] - ccx, c[1] - ccy) for fid, c in mcoords.items()}
            rmin = min(rads.values()); rmax = max(rads.values()); rspan = rmax - rmin
            # 半径变化够大=环形/同心 → 按半径(内→外涟漪);否则按主轴位置
            if rspan > 1e-6 and (rspan / (abs(rmax) + 1e-6)) > 0.25:
                for fid, r in rads.items():
                    spatial_phase[fid] = (r - rmin) / rspan
            else:
                xspan = max(mxs) - min(mxs); xmin = min(mxs)
                for fid, c in mcoords.items():
                    spatial_phase[fid] = ((c[0] - xmin) / xspan) if xspan > 1e-6 else 0.0
        # 缺坐标的设备回退序号相位
        for idx, fid in enumerate(ordered_m):
            spatial_phase.setdefault(fid, idx / max(1, len(ordered_m)))
        spread_turns = 0.5 + 1.0 * energy        # 阵列上相位铺开圈数(波浪密度)
        cx, cy = 128.0, 128.0
        for seq_i, fid in enumerate(ordered_m):
            p = patch.get(fid)
            if not p:
                continue
            uni = p["universe"]; chm = p["ch"]
            ax = chm.get("motor_x"); ay = chm.get("motor_y")
            axf = chm.get("motor_x_fine"); ayf = chm.get("motor_y_fine")
            # 空间相位偏移(按半径/主轴位置,非序号)→ 涟漪在阵列内传播
            dphase = spread_turns * spatial_phase.get(fid, 0.0)
            foff = int(round(dphase * nf))           # 折成帧偏移(路径循环错开)
            for i in range(nf):
                buf = _ensure(frame_bufs[i], uni)
                j = (i + foff) % nf                  # 相位错开取路径帧
                xv = float(xs[j]); yv = float(ys[j])
                # 16bit:有 fine 则拆 coarse/fine,防慢速台阶
                if ax is not None and 0 <= ax < 512:
                    if axf is not None and 0 <= axf < 512:
                        c, f = _logical_16(xv); buf[ax] = c; buf[axf] = f; _mark(uni, axf)
                    else:
                        buf[ax] = _clamp8(xv)
                    _mark(uni, ax)
                if ay is not None and 0 <= ay < 512:
                    if ayf is not None and 0 <= ayf < 512:
                        c, f = _logical_16(yv); buf[ay] = c; buf[ayf] = f; _mark(uni, ayf)
                    else:
                        buf[ay] = _clamp8(yv)
                    _mark(uni, ay)
    else:
        # 泵/阀/RGB/调光:shape_engine 生成空间值分布
        # 有序索引:把子集拼成一条序(奇偶两组各自连续,组内有序)
        ordered: List[str] = []
        group_of: Dict[str, int] = {}
        for gi, s in enumerate(subsets):
            for fid in s:
                if fid not in group_of:
                    group_of[fid] = gi
                    ordered.append(fid)
        is_static = (mechanism == "static")
        cycles = _speed_to_cycles(speed_dmx, duration)
        hi = int(round(255 * level))
        # 坐标按序列下标重映射(山形/渐变需真坐标算到质心距离)
        seq_coords = {seq_i: coords.get(fid, (0.0, 0.0)) for seq_i, fid in enumerate(ordered)}
        is_rgb = (usage == "RGB")
        # RGB 配色:由决策给定(white默认/pure纯色/color仅高潮),不再全程彩虹旋转
        color_mode = str(decision.get("color_mode", "white"))
        base_hue = float(decision.get("base_hue", (seed % 360) / 360.0))
        hue_span = 0.5 if not is_static else 0.18   # color模式色相跨度
        # ★ 时间包络:泵突发=脉冲(周期触底呼吸,复现真实CV~3),其余=缓起缓落 swell。
        #   静态保持(山形缓开)不加包络,由 clip.fade_in 体现。
        env_mode = "none"
        if not is_static:
            if usage == "泵":
                env_mode = "pulse"
            elif usage in ("RGB", "阀"):
                env_mode = "swell"
        pulse_rate = max(0.5, min(4.0, speed_dmx / 120.0))   # 脉冲频率随速度
        for i in range(n_frames):
            t_frac = (i / fps) / max(duration, 1e-6)
            env = _envelope(t_frac, env_mode, pulse_rate)
            if is_static:
                phase = 0.0       # 冻结:每帧同一形状(缓开保持由 clip.fade_in 体现)
            else:
                phase = (i / fps) * cycles / max(duration, 1e-6)   # 流动
            vals = _shape.shape_values(list(range(len(ordered))), seq_coords, shape,
                                       phase=phase, lo=0, hi=hi, seed=seed)
            nseq = max(1, len(ordered))
            for seq_i, fid in enumerate(ordered):
                p = patch.get(fid)
                if not p:
                    continue
                uni = p["universe"]; chm = p["ch"]
                v = int(vals.get(seq_i, 0))
                if (not is_static) and group_of.get(fid, 0) == 1:
                    v = hi - v   # 奇偶组反相呼应
                # 时间包络:脉冲/swell 调制亮度(泵触底呼吸/RGB缓起落),静态不调
                if env_mode != "none":
                    v = int(round(v * env))
                buf = _ensure(frame_bufs[i], uni)
                if is_rgb:
                    # ★ 配色对标标杆:白色为主,纯色次之,彩色仅高潮。
                    #   color_mode: white(R=G=B) / pure(单一基色相) / color(色相旋转,仅高潮)。
                    # ★亮度地板:真实RGB均值117-200,不能被空间波/包络压暗。
                    #   V = 地板 + (1-地板)*归一值,地板随level抬高,保证整体够亮。
                    floor = 0.45 + 0.35 * level     # level高→地板高(0.45~0.80)
                    vv = floor + (1.0 - floor) * max(0.0, min(1.0, v / 255.0))
                    if color_mode == "white":
                        w8 = int(round(vv * 255))
                        r = g = b = w8
                    elif color_mode == "pure":
                        # 纯色:固定基色相,饱和适中(不变色,整段一个色)
                        r, g, b = _hsv_to_rgb(base_hue, 0.80, vv)
                    else:  # color:高潮彩色流转
                        hue = (base_hue + hue_span * t_frac + 0.30 * seq_i / nseq) % 1.0
                        r, g, b = _hsv_to_rgb(hue, 0.90, vv)
                    rgb_map = {"R": r, "G": g, "B": b}
                    for ct in ch_types:
                        a = chm.get(ct)
                        if a is not None and 0 <= a < 512:
                            buf[a] = rgb_map.get(ct, v)
                            _mark(uni, a)
                else:
                    for ct in ch_types:
                        a = chm.get(ct)
                        if a is not None and 0 <= a < 512:
                            buf[a] = max(0, min(255, v))
                            _mark(uni, a)

    # 组装 DMXFrame(0基 timestamp)
    frames = []
    for i in range(n_frames):
        t = i / fps
        for uni, buf in frame_bufs[i].items():
            frames.append(DMXFrame(universe=uni, timestamp=t, data=buf.copy()))
    if not frames:
        return None
    clip_universes = sorted(acts.keys())
    acbu = {u: sorted(ch) for u, ch in acts.items()}
    clip = Clip(
        track_id="", start_time=float(start_time), duration=float(duration),
        frames=frames, label=f"AUTO {usage}",
        clip_universes=clip_universes or [1],
        active_channels_by_universe=acbu,
        fade_in=float(decision.get("fade_in", 0.6) or 0.6),
        fade_out=float(decision.get("fade_out", 0.6) or 0.6),
        playback_rate=1.0, source_duration=float(duration),
    )
    # 分组追逐过渡 + 方向 + 电机相位过渡
    if bool(decision.get("use_sequence", False)):
        clip.fade_groups = 4
        clip.fade_group_sequence_mode = "sequential"
        clip.fade_direction = "ltr" if direction == "L2R" else "rtl"
        clip.fade_in_direction = clip.fade_direction
    if bool(decision.get("use_motor_phase", False)):
        clip.motor_transition_phase_enabled = True
    return clip


def _clamp8(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _envelope(t_frac: float, mode: str, rate: float = 1.0) -> float:
    """时间包络 ∈[0,1]。t_frac=clip内进度0..1。
    pulse=周期触底脉冲(泵突发呼吸,复现真实零值占比);swell=缓起缓落(attack/decay)。"""
    t = max(0.0, min(1.0, t_frac))
    if mode == "pulse":
        ph = (t * max(0.5, rate)) % 1.0
        return max(0.0, math.exp(-3.0 * ph))   # 起点1指数衰减到~0.05,周期触底
    if mode == "swell":
        if t < 0.2:
            return t / 0.2
        if t > 0.75:
            return max(0.0, (1.0 - t) / 0.25)
        return 1.0
    return 1.0


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """HSV(各0..1)→ RGB(各0..255 int)。用于RGB声部真彩色流转。

    R183 hsv 统一：薄封装 src/utils/color_math.py（h%1.0 + s/v 钳位 + round
    口径，与 CE C 侧 `ce_vc_hsv_to_rgb` 同一逐位契约——fuzz 探针
    render_lab/bench/ce_fuzz_vcrender.py 继续经本函数对拍 C 内核）。
    """
    return _hsv_shared(h, s, v)


def _logical_16(v: float) -> Tuple[int, int]:
    """0..255 浮点逻辑值 → (coarse, fine) 16bit 字节,精度 1/256。"""
    logical = max(0.0, min(255.0, float(v)))
    v16 = 65535 if logical >= 255.0 else int(round(logical * 256.0))
    v16 = max(0, min(65535, v16))
    return (v16 >> 8) & 0xFF, v16 & 0xFF


def _voice_member_ids(plan: Any, vkey: str, voices: Dict[str, Any]) -> List[str]:
    """某声部的渲染成员集:优先 plan.voice_meta 里 bake 收敛的 scoped member_ids,
    缺失(旧方案/未 scope)回退到画像全量枚举。去重保序,统一 str。"""
    meta = (getattr(plan, "voice_meta", {}) or {}).get(vkey) or {}
    scoped = meta.get("member_ids")
    if scoped:
        return list(dict.fromkeys(str(x) for x in scoped))
    v = voices.get(vkey)
    if v is None:
        return []
    member_ids: List[str] = []
    for g in v.groups:
        member_ids.extend(str(x) for x in (g.member_ids or []))
    return list(dict.fromkeys(member_ids))


def render_voice_arrangement(
    plan: Any,
    project: Any,
    fps: int = FPS_DEFAULT,
) -> Dict[str, List[Any]]:
    """把整份 BakedVoiceArrangement 渲染成 {voice_key: [Clip,...]}(每动作窗口一个clip,稀疏)。

    需要 project 提供 fixtures/fixture_defs(建patch)+ 坐标(topview_state)+ 编组(声部成员)。
    """
    from .project_profile import build_project_profile

    patch = build_patch(project)
    defs, def_of = _build_fixture_defs(project)
    # 坐标:topview 各页首个非零(与逆向脚本同口径)
    coords: Dict[str, Tuple[float, float]] = {}
    for pg in (getattr(project, "topview_state", None) or {}).get("pages", []) or []:
        for fid, v in (pg.get("fixtures", {}) or {}).items():
            if fid in coords:
                continue
            if isinstance(v, dict) and v.get("x") is not None and v.get("y") is not None:
                try:
                    x = float(v["x"]); y = float(v["y"])
                    if abs(x) > 1.0 or abs(y) > 1.0:
                        coords[fid] = (x, y)
                except (TypeError, ValueError):
                    pass
    # 声部成员:优先 plan.voice_meta 里 bake 收敛的 scoped member_ids,
    # 旧方案/未 scope 时回退到画像全量反查。
    profile = build_project_profile(project)
    from .voice_model import enumerate_voices
    voices = {v.key: v for v in enumerate_voices(profile)}

    seed = int(getattr(plan, "seed", 0) or 0)
    out: Dict[str, List[Any]] = {}
    for vkey, windows in (getattr(plan, "voices", {}) or {}).items():
        v = voices.get(vkey)
        if v is None:
            continue
        member_ids = _voice_member_ids(plan, vkey, voices)
        if not member_ids:
            continue
        clips = []
        for w in windows:
            dec = getattr(w, "decision", {}) or {}
            t0 = float(getattr(w, "t_start", 0.0) or 0.0)
            t1 = float(getattr(w, "t_end", 0.0) or 0.0)
            dur = float(dec.get("clip_dur", t1 - t0) or (t1 - t0))
            dur = min(max(dur, 0.5), max(0.5, t1 - t0))   # 不超出段
            clip = render_voice_window(
                dec, member_ids, patch, coords, v.usage, v.voice_class,
                start_time=t0, duration=dur, fps=fps, seed=seed,
                defs=defs, def_of=def_of)
            if clip is not None:
                clips.append(clip)
        if clips:
            out[vkey] = clips
    # ★一设备多轨:把适合拆分的声部按拓扑切成多条子轨(错开/交错过渡)。
    out = _split_into_subtracks(out, plan, voices, patch, coords, fps, seed, defs, def_of)
    return out


def _split_into_subtracks(rendered: Dict[str, List[Any]], plan: Any, voices: Dict[str, Any],
                          patch: Dict, coords: Dict, fps: int, seed: int,
                          defs: Optional[Dict[str, Any]] = None,
                          def_of: Optional[Dict[str, str]] = None) -> Dict[str, List[Any]]:
    """对适合的声部,把单轨拆成多条子轨(每条=一个拓扑子集,各自错开进场+相位)。

    触发条件(任一):窗口决策用了 odd_even/halves 拓扑(本就两组呼应)、
    或该声部设备数较多且存在高能窗口(精细化)。不适合的保持单轨。
    子轨 key = f"{vkey} ·分{i+1}",成员稳定(整首同一拆分),便于交错过渡。
    """
    out: Dict[str, List[Any]] = {}
    windows_by_voice = getattr(plan, "voices", {}) or {}
    for vkey, clips in rendered.items():
        v = voices.get(vkey)
        wins = windows_by_voice.get(vkey, [])
        # 判定是否拆分 + 拆几条
        n_split = 1
        if v is not None and wins:
            member_ids = _voice_member_ids(plan, vkey, voices)
            uses_pair = any((getattr(w, "decision", {}) or {}).get("topo_kind") in ("odd_even", "halves")
                            for w in wins)
            hi_energy = any(float((getattr(w, "decision", {}) or {}).get("level", 0)) >= 0.5 for w in wins)
            ndev = len(member_ids)
            # 奇偶/对半拓扑→2轨;设备较多(≥16)且有中高能→也拆2轨精细;否则单轨
            if uses_pair and ndev >= 8:
                n_split = 2
            elif ndev >= 16 and hi_energy and v.voice_class in ("matrix_rgb", "fountain_rgb", "pump"):
                n_split = 2
        if n_split <= 1:
            out[vkey] = clips
            continue
        # 用拓扑把成员稳定切成 n_split 个有序子集
        try:
            subs = _topo.select_subset(member_ids, coords,
                                       "odd_even" if n_split == 2 else "halves")
        except Exception:
            subs = None
        if not subs or len(subs) < n_split:
            out[vkey] = clips
            continue
        subs = subs[:n_split]
        v_usage = v.usage; v_class = v.voice_class
        for si, sub_members in enumerate(subs):
            sub_key = f"{vkey} ·分{si+1}"
            sub_clips = []
            # 子轨整体错开:第2条延迟半个动作时长,形成交错进场
            stagger = si * 0.35
            for w in wins:
                dec = dict(getattr(w, "decision", {}) or {})
                t0 = float(getattr(w, "t_start", 0.0) or 0.0) + stagger
                t1 = float(getattr(w, "t_end", 0.0) or 0.0)
                dur = float(dec.get("clip_dur", t1 - t0) or (t1 - t0))
                dur = min(max(dur, 0.5), max(0.5, t1 - t0))
                if dur < 0.5:
                    continue
                # 第2条子轨相位反相(交错过渡):色相/方向偏移
                if si == 1:
                    dec["direction"] = "R2L" if dec.get("direction") == "L2R" else "L2R"
                    dec["base_hue"] = (float(dec.get("base_hue", 0.0)) + 0.12) % 1.0
                clip = render_voice_window(dec, list(sub_members), patch, coords,
                                           v_usage, v_class, start_time=t0, duration=dur,
                                           fps=fps, seed=seed + si * 101,
                                           defs=defs, def_of=def_of)
                if clip is not None:
                    sub_clips.append(clip)
            if sub_clips:
                out[sub_key] = sub_clips
    return out
