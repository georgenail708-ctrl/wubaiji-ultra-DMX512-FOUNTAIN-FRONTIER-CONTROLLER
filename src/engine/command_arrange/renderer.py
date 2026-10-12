"""
command_arrange.renderer — IR (ClipStmt) → 时间线 Clip

把解析出的命令块渲染成 models.Clip(逐帧 DMXFrame)+ 完整 clip 元数据。
纯 core,无 PyQt、不依赖 EffectEngine(沙箱直接产出 compute fn,避免 core→ui 依赖)。

流程:
  resolve_selector  选择器(全局号/组名/组内下标)→ 有序 fixture id 列表
  build_patch       复用 voice_clip_renderer.build_patch(地址映射)
  sandbox 编译       每个通道体 → fn(ef,t,sorted_fx,fdefs,p,fps)
  逐帧执行          填 ef.values[orig_i][ch] → 经 patch 写 DMX buffer(电机 16bit 拆分)
  设 clip 字段       命令里的过渡/分组/坐标系/灯跟踪/保持… 一一写到 Clip(回放引擎照旧消费)

不卡顿保证:
  - 单 clip 帧数硬上限 MAX_FRAMES;超长 clip 自动降 fps 或截断并 log。
  - 整次渲染总帧数软上限 MAX_TOTAL_FRAMES;触顶停止并报告。
  - 渲染本身是纯计算,UI 层会放后台线程跑(主线程只挂轨)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import ir
from . import sandbox as _sandbox

# 复用 voice 渲染器的 patch 构建与 16bit 拆分(同一真源,防漂移)
from ..auto_arrange.voice_clip_renderer import build_patch as _build_patch, _logical_16
# 翼/块:复用全局 wing_block 工具(与 effect 引擎/轨迹曲线同一真源)
try:
    from ...utils.wing_block import build_wing_block_units as _build_units
except ImportError:
    try:
        from utils.wing_block import build_wing_block_units as _build_units
    except ImportError:
        from src.utils.wing_block import build_wing_block_units as _build_units

FPS_DEFAULT = 40
MAX_FRAMES = 8000          # 单 clip 帧数上限(@40fps = 200s);超出降采样
MAX_TOTAL_FRAMES = 240000  # 整次生成总帧数软上限,防一次写爆内存
TRACK_PREFIX = "CMD·"      # 生成轨道统一前缀,便于辨识/批量删

# 电机轴(可能有 _fine 精调对)
_MOTOR_AXES = ("motor_x", "motor_y", "motor_z")


def _load_models():
    try:
        from core.models import Clip, DMXFrame
    except ImportError:
        try:
            from src.core.models import Clip, DMXFrame
        except ImportError:
            from ...core.models import Clip, DMXFrame
    return Clip, DMXFrame


# ─────────────────────────────────────────────────────────────────────────────
#  项目索引:全局号 / 组名 / 组内下标
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ProjectIndex:
    fixture_ids: List[str] = field(default_factory=list)          # 全局序(1-based 对应 idx+1)
    group_by_name: Dict[str, List[str]] = field(default_factory=dict)  # 组名/小写名/id → 成员id序
    coords: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    patch: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def build_coords(project: Any) -> Dict[str, Tuple[float, float]]:
    """topview 各页首个非零坐标(与 voice 渲染器同口径)。"""
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
    return coords


def build_project_index(project: Any) -> ProjectIndex:
    idx = ProjectIndex()
    # 全局序
    for fx in getattr(project, "fixtures", None) or []:
        fid = getattr(fx, "id", None) or (fx.get("id") if isinstance(fx, dict) else None)
        if fid is not None:
            idx.fixture_ids.append(fid)
    # 组(名/小写名/id 都可索引)
    for g in getattr(project, "fixture_groups", None) or []:
        gid = g.get("id", "") if isinstance(g, dict) else getattr(g, "id", "")
        gname = g.get("name", "") if isinstance(g, dict) else getattr(g, "name", "")
        mids = (g.get("fixture_ids", []) if isinstance(g, dict)
                else getattr(g, "fixture_ids", [])) or []
        mids = [str(m) for m in mids]
        for key in {str(gname), str(gname).strip().lower(), str(gid)}:
            if key:
                idx.group_by_name[key] = mids
    idx.coords = build_coords(project)
    try:
        idx.patch = _build_patch(project)
    except Exception:
        idx.patch = {}
    return idx


# ─────────────────────────────────────────────────────────────────────────────
#  选择器解析
# ─────────────────────────────────────────────────────────────────────────────
def _expand_ranges(ranges: Sequence[Tuple[int, int]]) -> List[int]:
    out: List[int] = []
    for lo, hi in ranges:
        if hi < lo:
            lo, hi = hi, lo
        out.extend(range(lo, hi + 1))
    return out


def resolve_selector(selector: ir.Selector, idx: ProjectIndex) -> Tuple[List[str], List[str]]:
    """选择器 → (有序去重 fixture id 列表, 警告list)。
    全局号超界/组名未知/下标越界 都记警告但不崩。"""
    out: List[str] = []
    warns: List[str] = []
    seen = set()

    def _add(fid: str):
        if fid and fid not in seen:
            seen.add(fid); out.append(fid)

    for term in (selector.terms or []):
        if term.kind == ir.SELECT_ALL:
            for fid in idx.fixture_ids:
                _add(fid)
        elif term.kind == ir.SELECT_TYPE:
            tref = str(term.type_ref or "")
            hit = 0
            for fid in idx.fixture_ids:
                p = idx.patch.get(fid)
                if p and tref in (p.get("ch") or {}):
                    _add(fid); hit += 1
            if hit == 0:
                warns.append(f"没有设备配接含通道类型「{tref}」")
        elif term.kind == ir.SELECT_GLOBAL:
            for num in term.expand_indices():
                if 1 <= num <= len(idx.fixture_ids):
                    _add(idx.fixture_ids[num - 1])
                else:
                    warns.append(f"全局编号 {num} 超出范围(共 {len(idx.fixture_ids)} 台)")
        elif term.kind == ir.SELECT_GROUP:
            ref = str(term.group_ref or "")
            members = (idx.group_by_name.get(ref)
                       or idx.group_by_name.get(ref.strip().lower()))
            if members is None:
                warns.append(f"未知设备组: {ref}")
                continue
            for fid in members:
                _add(fid)
        elif term.kind == ir.SELECT_GROUP_INDEX:
            ref = str(term.group_ref or "")
            members = (idx.group_by_name.get(ref)
                       or idx.group_by_name.get(ref.strip().lower()))
            if members is None:
                warns.append(f"未知设备组: {ref}")
                continue
            for num in term.expand_indices():
                if 1 <= num <= len(members):
                    _add(members[num - 1])
                else:
                    warns.append(f"组 {ref} 内下标 {num} 越界(共 {len(members)} 台)")
    return out, warns


# ─────────────────────────────────────────────────────────────────────────────
#  渲染一个 clip
# ─────────────────────────────────────────────────────────────────────────────
class _FX:
    __slots__ = ("id",)
    def __init__(self, fid): self.id = fid


class _EF:
    """轻量 EffectFrame 替身(沙箱 fn 只用 .values / .t)。"""
    __slots__ = ("values", "t")
    def __init__(self, n: int, t: float):
        self.values = [{} for _ in range(n)]
        self.t = t


@dataclass
class RenderReport:
    n_tracks: int = 0
    n_clips: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _norm_group_pauses(pauses, count, fallback):
    """规范化组间停顿列表到长度 count(对齐 models.normalize_group_pauses 行为,本地简版)。"""
    out = [max(0.0, float(p)) for p in (pauses or [])][:count]
    while len(out) < count:
        out.append(max(0.0, float(fallback)))
    return out


def _apply_clip_fields(clip: Any, stmt: ir.ClipStmt, members: List[str],
                       coords: Dict[str, Tuple[float, float]],
                       written_cts: set) -> List[str]:
    """把命令块里的全部右击可设置项写到 Clip(回放引擎照旧消费)。返回警告列表。

    含三处引擎耦合修正(否则命令写了却被引擎静默忽略):
      seq        : 必须设 fade_group_single_duration 并按批次展开 duration,否则引擎按并行播。
      dir        : 方向但无淡入淡出时自动补 default_fade,否则引擎忽略方向(对齐右击菜单)。
      track_light: 启用但 clip 没写 source(pump/valve)通道时灯会被跟踪逻辑压黑 → 警告并禁用。
    """
    warns: List[str] = []
    clip.fade_in = max(0.0, float(stmt.fade_in))
    clip.fade_out = max(0.0, float(stmt.fade_out))
    d = stmt.direction or "none"
    clip.fade_direction = d
    clip.fade_in_direction = d
    clip.fade_out_direction = d
    groups = max(1, int(stmt.sets))
    clip.fade_groups = groups
    seq_mode = "sequential" if str(stmt.sequence_mode).lower() == "sequential" else "parallel"
    clip.fade_group_sequence_mode = seq_mode
    clip.fade_group_pause = max(0.0, float(stmt.pause))
    batches = max(0, int(stmt.batches))
    clip.fade_group_sequence_batches = min(groups, batches) if batches > 0 else 0
    clip.fade_group_spatial_scope = ir.normalize_scope(stmt.scope) or "follow"
    clip.seq_delay_in = max(0.0, float(stmt.seq_delay_in))
    clip.seq_delay_out = max(0.0, float(stmt.seq_delay_out))
    clip.fade_type_ratio_in = {str(k): float(v) for k, v in (stmt.fade_ratio_in or {}).items()}
    clip.fade_type_ratio_out = {str(k): float(v) for k, v in (stmt.fade_ratio_out or {}).items()}

    # ── #5 顺序分组(R12 2026-08-13): DSL 显式 dur = 内容时长, 不再展开 duration;
    #    扫掠一律走 seq_delay(与过渡翼同一真源); 顺序分组未显式给延迟时默认整窗扫掠。 ──
    batch_count = (clip.fade_group_sequence_batches
                   if clip.fade_group_sequence_batches > 0 else groups)
    batch_count = max(1, min(groups, batch_count))
    group_pauses = _norm_group_pauses(stmt.pauses, max(0, batch_count - 1), clip.fade_group_pause)
    clip.fade_group_pauses = group_pauses
    base_dur = max(0.05, float(clip.duration))
    if seq_mode == "sequential" and groups > 1:
        if clip.seq_delay_in <= 1e-6:
            clip.seq_delay_in = base_dur
        clip.source_duration = base_dur
        clip.playback_rate = 1.0
    else:
        clip.source_duration = float(clip.duration)
    clip.fade_group_single_duration = 0.0

    # ── #6 方向但无淡入淡出 → 自动补 default_fade(否则引擎忽略方向) ──
    dur_now = max(0.0, float(clip.duration))
    if d != "none":
        seq_active = (seq_mode == "sequential" and groups > 1)
        has_delay = clip.seq_delay_in > 1e-6 or clip.seq_delay_out > 1e-6
        if not seq_active and not has_delay:
            default_fade = min(1.0, dur_now)
            if default_fade > 1e-4:
                if clip.fade_in <= 1e-4:
                    clip.fade_in = default_fade
                if clip.fade_out <= 1e-4:
                    clip.fade_out = default_fade
    clip.fade_in = min(clip.fade_in, dur_now)
    clip.fade_out = min(clip.fade_out, dur_now)

    # 灯跟踪
    src = "valve" if str(stmt.track_light_source).lower() == "valve" else "pump"
    if stmt.track_light_enabled and src not in written_cts:
        # ── #7 启用灯跟踪但本 clip 没写 source 通道 → 跟踪逻辑会把 RGB 压黑 ──
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」灯跟踪已启用但未写 {src} 通道,"
                     f"灯会被跟踪逻辑压黑——已自动关闭灯跟踪(请在本 clip 写 {src} 通道再开)")
        clip.light_tracking_enabled = False
    else:
        clip.light_tracking_enabled = bool(stmt.track_light_enabled)
    clip.light_tracking_source = src
    clip.light_tracking_threshold = max(0, min(255, int(stmt.track_light_threshold)))
    clip.light_tracking_attack = max(0.01, float(stmt.track_light_attack))
    clip.light_tracking_release = max(0.01, float(stmt.track_light_release))
    clip.light_tracking_color = str(stmt.track_light_color or "#FFFFFF")
    # 电机相位
    clip.motor_transition_phase_enabled = bool(stmt.motor_phase_enabled)
    clip.motor_transition_phase_amount = max(0.0, min(10.0, float(stmt.motor_phase_amount)))
    # 保持 / 速度 / 平滑 / 录制接收(注意:顺序分组已设过 playback_rate/source_duration,这里仅在非顺序时用用户 rate)
    hm = ir.normalize_hold(stmt.hold_mode) or "none"
    clip.hold_mode = hm
    clip.hold_last_frame = hm != "none"
    if not (seq_mode == "sequential" and groups > 1):
        clip.playback_rate = max(0.001, min(100.0, float(stmt.rate)))
    clip.smooth_preset = str(stmt.smooth_preset or "")
    clip.record_output_source_enabled = bool(stmt.rec_src_enabled)
    # 空间基准(供方向/分组按坐标排序)
    clip.base_order_fixture_ids = [str(f) for f in members]
    clip.base_fixture_positions = {
        str(f): [float(coords[f][0]), float(coords[f][1])]
        for f in members if f in coords
    }
    # 方向/分组设了但全无空间坐标 → 引擎只能退化为选择顺序,提示用户
    if (d != "none" or groups > 1) and not clip.base_fixture_positions:
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」用了方向/分组但所选设备无 TopView 坐标,"
                     f"将按选择顺序退化(空间方向可能不明显)")
    return warns


def render_clip(stmt: ir.ClipStmt, idx: ProjectIndex, fps: int,
                start_time: float, frame_budget: int) -> Tuple[Optional[Any], List[str], int]:
    """渲染单个 ClipStmt → (Clip 或 None, 警告list, 用掉的帧数)。"""
    Clip, DMXFrame = _load_models()
    warns: List[str] = []

    members, sel_warns = resolve_selector(stmt.selector, idx)
    warns.extend(sel_warns)
    if not members:
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」未选中任何设备,跳过")
        return None, warns, 0
    if not stmt.bodies:
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」没有任何通道效果,跳过")
        return None, warns, 0

    duration = max(0.1, float(stmt.dur))
    # 帧数:受单 clip 上限 + 剩余预算约束;必要时降 fps
    eff_fps = float(fps)
    n_frames = int(round(duration * eff_fps)) + 1
    if n_frames > MAX_FRAMES:
        eff_fps = MAX_FRAMES / duration
        n_frames = MAX_FRAMES
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」过长,fps 降到 {eff_fps:.1f} 以控内存")
    if n_frames > frame_budget:
        # 触及总预算:降采样到预算内,但帧仍**铺满整段时长**(t=i/eff_fps 跨 duration),
        # 否则 clip 只在前一小段有内容、其余冻结,且 end_time 用声明时长会让后续链式 clip 起点错位。
        n_frames = max(1, frame_budget)
        eff_fps = max(1e-6, (n_frames - 1) / duration) if n_frames > 1 else max(1e-6, 1.0 / duration)
        warns.append(f"行{stmt.src_line}: 触及总帧数上限,clip「{stmt.label}」已降采样到 {eff_fps:.1f}fps(仍覆盖全时长)")

    params = dict(stmt.params or {})
    params["_dur"] = duration

    # 编译所有通道体(一次),先 probe 防带病渲染
    compiled = []
    for body in stmt.bodies:
        errs = _sandbox.validate_code(body.code, body.kind)
        if errs:
            warns.append(f"行{body.src_line or stmt.src_line}: 代码校验失败: {'; '.join(errs)}")
            continue
        perrs = _sandbox.probe_compile(body, params)
        if perrs:
            warns.append(f"行{body.src_line or stmt.src_line}: 执行预检失败: {'; '.join(perrs)}")
            continue
        try:
            fn = _sandbox.compile_channel_body(body, params, idx.coords)
            compiled.append(fn)
        except Exception as e:
            warns.append(f"行{body.src_line or stmt.src_line}: 编译异常: {e}")
    if not compiled:
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」无可用通道体,跳过")
        return None, warns, 0

    # 翼/块:把设备折成「虚拟同步单元」。每单元出一个代表喂给效果函数(i=单元序号,
    # n=单元数),算完把该单元的值广播回单元内每台设备。wings=1&block=1 时每台自成
    # 一个单元 = 旧逐台行为(零回归)。与 effect 引擎 / 轨迹曲线同一 wing_block 真源。
    from .wing_attributes import units as statement_units
    units = statement_units(stmt, members)
    unit_reps = [(ui, _FX(u[0])) for ui, u in enumerate(units) if u]

    # 悬空设备:选中但无配接(def 缺失/跨域未配) → 警告,不静默丢
    missing = [m for m in members if m not in idx.patch]
    if missing:
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」有 {len(missing)} 台选中设备无配接,已跳过")

    # 逐帧 buffer
    frame_bufs: List[Dict[int, np.ndarray]] = [dict() for _ in range(n_frames)]
    written_cts: set = set()   # 实际写出的通道类型(供 track_light source 校验)
    active_cts: set = set()    # 有活跃输出值的通道类型(≠0且电机≠128),用于 active_channels_by_universe

    def _ensure(buf_map, uni):
        b = buf_map.get(uni)
        if b is None:
            b = np.zeros(512, dtype=np.uint8); buf_map[uni] = b
        return b

    # ── 性能(#perf):预解析每个单元的写目标(frame-invariant),热路径不再每帧 patch.get。
    #   unit_targets[ui] = [(universe, chm dict), ...](该单元内有配接的设备)。
    unit_targets: List[List[Tuple[int, dict]]] = []
    for unit in units:
        tgts = []
        for fid in unit:
            p = idx.patch.get(fid)
            if p:
                tgts.append((p["universe"], p["ch"]))
        unit_targets.append(tgts)
    # 活动通道集(acts)/written_cts 在标量 clip 里逐帧不变 → 只在第0帧标记一次;
    # 含 def 体时通道可能逐帧变化 → 每帧标记。
    mark_every_frame = any(b.kind == ir.BODY_DEF for b in stmt.bodies)

    n_units = len(unit_reps)
    frame_err_logged = False
    for i in range(n_frames):
        t = i / eff_fps
        ef = _EF(n_units, t)
        for fn in compiled:
            try:
                fn(ef, t, unit_reps, {}, params, eff_fps)
            except Exception as e:
                if not frame_err_logged:
                    warns.append(f"行{stmt.src_line}: 运行期异常: {e}")
                    frame_err_logged = True
        do_mark = mark_every_frame or i == 0
        ef_values = ef.values
        bufmap = frame_bufs[i]
        # 映射到 DMX:单元值广播到单元内每台(用预解析目标)
        for ui in range(n_units):
            chvals = ef_values[ui]
            if not chvals:
                continue
            for uni, chm in unit_targets[ui]:
                buf = _ensure(bufmap, uni)
                for ct, raw in chvals.items():
                    a = chm.get(ct)
                    if a is None or not (0 <= a < 512):
                        continue
                    v = raw if 0 <= raw <= 255 else (0 if raw < 0 else 255)
                    if ct in _MOTOR_AXES:
                        fine = chm.get(ct + "_fine")
                        if fine is not None and 0 <= fine < 512:
                            c, f = _logical_16(float(v))
                            buf[a] = c; buf[fine] = f
                            if do_mark:
                                written_cts.add(ct)
                                if v not in (0, 128):
                                    active_cts.add(ct)
                            continue
                    buf[a] = v
                    if do_mark:
                        written_cts.add(ct)
                        if v != 0:
                            active_cts.add(ct)

    # 组帧 + 逐 universe 去重：只在该 universe 数据相对上一发出帧「变化」时才发新帧。
    # 回放 _resolve_frame_index 按 universe 用 bisect 取「≤t 的最后一帧」=天然 hold-last-value，
    # 故省略中间不变帧不改变任何回放输出，却大幅降低内存/序列化体积(长 clip 静态段尤甚)。
    frames = []
    last_buf: Dict[int, np.ndarray] = {}   # universe -> 上一发出帧的 data
    for i in range(n_frames):
        t = i / eff_fps
        for uni, buf in frame_bufs[i].items():
            prev = last_buf.get(uni)
            if prev is not None and np.array_equal(prev, buf):
                continue   # 该 universe 本帧与上次发出完全相同 → 跳过(回放会 hold)
            frames.append(DMXFrame(universe=uni, timestamp=t, data=buf.copy()))
            last_buf[uni] = buf
    if not frames or not active_cts:
        warns.append(f"行{stmt.src_line}: clip「{stmt.label}」未写出任何有效通道"
                     f"(检查通道名是否匹配设备配接 / 地址是否越界),跳过")
        return None, warns, 0

    # Compute active_channels_by_universe from fixture patch + active channel types,
    # not from _mark() — avoids marking zero/center values as active (performance).
    _tmp_acbu: Dict[int, set] = {}
    for targets in unit_targets:
        for uni, chm in targets:
            for ct in active_cts:
                a = chm.get(ct)
                if a is not None and 0 <= a < 512:
                    _tmp_acbu.setdefault(uni, set()).add(a + 1)
                if ct in _MOTOR_AXES:
                    fine = chm.get(ct + "_fine")
                    if fine is not None and 0 <= fine < 512:
                        _tmp_acbu.setdefault(uni, set()).add(fine + 1)
    acbu = {u: sorted(chs) for u, chs in _tmp_acbu.items()}
    clip_universes = sorted(acbu.keys())
    label = stmt.label or f"CMD {stmt.src_line}"
    clip = Clip(
        track_id="", start_time=float(start_time), duration=float(duration),
        frames=frames, label=label,
        clip_universes=clip_universes,
        active_channels_by_universe=acbu,
        is_preset_clip=True,
        preset_name="cmd")
    warns.extend(_apply_clip_fields(clip, stmt, members, idx.coords, written_cts))
    return clip, warns, n_frames


# ─────────────────────────────────────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────────────────────────────────────
def _default_track_name(stmt: ir.ClipStmt) -> str:
    """块头没写 track 时,按通道类型给默认轨名。"""
    chs = set()
    for b in stmt.bodies:
        if b.kind == ir.BODY_SCALAR and b.channel:
            chs.add(b.channel)
    if not chs:
        return "命令"
    if {"R", "G", "B", "W", "dimmer"} & chs:
        return "灯"
    if "pump" in chs:
        return "泵"
    if "valve" in chs:
        return "阀"
    if chs & set(_MOTOR_AXES):
        return "电机"
    return "命令"


def render(clips_or_result, project: Any, fps: int = FPS_DEFAULT) -> Tuple[Dict[str, List[Any]], RenderReport]:
    """命令块 → {轨道名: [Clip]} + 报告。

    clips_or_result: ir.ParseResult 或 List[ClipStmt]。
    轨道名带 TRACK_PREFIX 前缀(追加模式,便于辨识/批量删)。
    未显式写 at 的 clip 接在同轨上一条之后(链式)。
    """
    if isinstance(clips_or_result, ir.ParseResult):
        stmts = list(clips_or_result.clips)
        report = RenderReport(errors=[str(e) for e in clips_or_result.errors])
        # 解析期非致命提示(dur 默认等)也带进渲染报告,让用户在日志里看到
        for w in (getattr(clips_or_result, "warnings", None) or []):
            report.warnings.append(str(w))
    else:
        stmts = list(clips_or_result or [])
        report = RenderReport()

    idx = build_project_index(project)
    out: Dict[str, List[Any]] = {}
    track_cursor: Dict[str, float] = {}   # 轨道名 → 当前链式光标(秒)
    budget = MAX_TOTAL_FRAMES

    for stmt in stmts:
        tname = TRACK_PREFIX + (stmt.track_name or _default_track_name(stmt))
        # 起始时间:显式 at 用之;否则接在该轨上一条之后
        if stmt.at_explicit:
            start = max(0.0, float(stmt.at))
        else:
            start = track_cursor.get(tname, 0.0)
        if budget <= 0:
            report.warnings.append("已达总帧数上限,后续 clip 未生成")
            break
        clip, warns, used = render_clip(stmt, idx, fps, start, budget)
        report.warnings.extend(warns)
        if clip is None:
            continue
        budget -= used
        out.setdefault(tname, []).append(clip)
        track_cursor[tname] = clip.end_time()
        report.n_clips += 1

    report.n_tracks = len(out)
    return out, report
