# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


MOTOR_X = "motor_x"
MOTOR_Y = "motor_y"
MOTOR_Z = "motor_z"
MOTOR_X_FINE = "motor_x_fine"
MOTOR_Y_FINE = "motor_y_fine"
MOTOR_Z_FINE = "motor_z_fine"
SYNC_MODE = "sync"
DEFAULT_MODE = "default"
LOGICAL_CENTER = 128.0
# ★ Z 轴（自转/扇面）角度范围缺省：标准连续电机（Value_Type=3）DMX 0-255
#   线性映射到 [MinAngle, MaxAngle]，DMXHomeValue=128≈0°。设备 def 无 motor_ranges
#   （旧工程/手动配接）时回退 ±180°；实测 MDD Yaw ±180°（16bit）、副池三维扇子
#   Rotation ±270°（8bit）由导入解析进 motor_ranges。
DEFAULT_Z_MIN_DEG = -180.0
DEFAULT_Z_MAX_DEG = 180.0
@dataclass(frozen=True)
class XYReference:
    fixture_id: str
    universe: int
    x_addr: int
    y_addr: int
    x_fine_addr: Optional[int] = None
    y_fine_addr: Optional[int] = None
    angle_deg: float = 0.0
    base_angle_deg: float = 0.0
    y_axis_sign: float = 1.0
    mode: str = SYNC_MODE
    # ★ Z 轴（自转/扇面）扇面基准（2026-08-04 用户需求）：无 motor_z 的设备为
    #   None/缺省，完全不写——向后兼容。z_min/z_max_deg 来自导入解析的电机角度
    #   范围（MDD ±180 / 扇子 ±270），z_angle_deg 为扇面目标角（画布角空间，
    #   TopView「扇面角度」控件，默认 0=水平 +X）。
    z_addr: Optional[int] = None
    z_fine_addr: Optional[int] = None
    z_min_deg: float = DEFAULT_Z_MIN_DEG
    z_max_deg: float = DEFAULT_Z_MAX_DEG
    z_angle_deg: float = 0.0
    # ★ 圆排多设备统一化（2026-08-04 用户需求，通用机制）：星形外顶点圆簇设备
    #   （_star_circle_override 门控，副池三维扇子=两套各 5 台）同步后全部设备
    #   统一世界方向——静止指向 = 全局角（不再逐台复刻安装朝向的 3+2 相反摆放），
    #   推 X = 世界左右、推 Y = 世界前后（全部一致），扇面全水平（扇面角默认
    #   0°=水平+X）。引擎语义差异：
    #   - angle_deg（世界目标角）= 偏移 + 全局角（不再叠加 base，基准由
    #     base_angle_deg 单独承载 → 静止电机 = 目标 − 安装 → 世界波束 = 目标）；
    #   - 内容旋转参数 = base − angle（抵消安装朝向，内容方向 = 世界偏移）。
    uniform_world: bool = False
    # ★ 静态旋转参数缓存（2026-08-12 候选点#12）：录制 25ms 轮询每 tick 对
    #   全部 sync refs 重算 math.radians/cos/sin/mode 归一化（3000 refs 实测
    #   ~0.3ms/tick 纯静态重算）。cos/sin 纯函数于 frozen 字段 → build 期算
    #   一次每 tick 零重算。None = 未预计算（手动构造 ref）→ 变换侧回退旧
    #   公式，逐字节零变化。
    mode_is_sync: Optional[bool] = None
    cos_rad: Optional[float] = None
    sin_rad: Optional[float] = None
    y_sign_eff: Optional[float] = None


def normalize_reference_mode(mode: Any) -> str:
    text = str(mode or DEFAULT_MODE).strip().lower()
    if text in {SYNC_MODE, "synced", "synchronized", "sync_mode"}:
        return SYNC_MODE
    return DEFAULT_MODE


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        logger.debug("Failed to convert value %r to int, using default %r", v, default)
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        logger.debug("Failed to convert value %r to float, using default %r", v, default)
        return default


def _as_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def channel_type_key(ch_type: Any) -> str:
    raw = ch_type
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("ch_type", raw.get("type", raw)))
    raw = getattr(raw, "value", raw)
    raw = getattr(raw, "ch_type", raw)
    raw = getattr(raw, "type", raw)
    text = str(raw or "").strip().lower()
    text = text.replace("-", "_").replace(" ", "_")
    aliases = {
        "pan": MOTOR_X,
        "x": MOTOR_X,
        "x_axis": MOTOR_X,
        "axis_x": MOTOR_X,
        "motor_pan": MOTOR_X,
        "pan_coarse": MOTOR_X,
        "motor_x_coarse": MOTOR_X,
        "tilt": MOTOR_Y,
        "y": MOTOR_Y,
        "y_axis": MOTOR_Y,
        "axis_y": MOTOR_Y,
        "motor_tilt": MOTOR_Y,
        "tilt_coarse": MOTOR_Y,
        "motor_y_coarse": MOTOR_Y,
        "pan_fine": MOTOR_X_FINE,
        "motor_pan_fine": MOTOR_X_FINE,
        "motor_x_fine": MOTOR_X_FINE,
        "tilt_fine": MOTOR_Y_FINE,
        "motor_tilt_fine": MOTOR_Y_FINE,
        "motor_y_fine": MOTOR_Y_FINE,
        # Z 轴（自转/扇面）：Yaw/Rotation 通道在导入时已改判 motor_z，
        # 别名兜底其他来源（如手动配接的 def 直接命名）。
        "yaw": MOTOR_Z,
        "rotation": MOTOR_Z,
        "z": MOTOR_Z,
        "z_axis": MOTOR_Z,
        "motor_yaw": MOTOR_Z,
        "motor_z_coarse": MOTOR_Z,
        "yaw_fine": MOTOR_Z_FINE,
        "rotation_fine": MOTOR_Z_FINE,
        "z_fine": MOTOR_Z_FINE,
        "motor_yaw_fine": MOTOR_Z_FINE,
        "motor_z_fine": MOTOR_Z_FINE,
    }
    return aliases.get(text, text)


def _channel_offsets(channels: Iterable[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for idx, ch_type in enumerate(list(channels or [])):
        key = channel_type_key(ch_type)
        if key and key not in out:
            out[key] = int(idx)
    return out


def has_xy_channels(channels: Iterable[Any]) -> bool:
    offsets = _channel_offsets(channels)
    return MOTOR_X in offsets and MOTOR_Y in offsets


def build_xy_reference_map(project: Any, topview_state: Optional[dict] = None) -> Dict[int, List[XYReference]]:
    """Build per-universe sync-reference output transforms from project state.

    Only fixtures with both motor_x and motor_y channels are eligible.  Fine
    channels are optional and, when present, are handled as part of the same
    logical axis.
    """
    state = topview_state if isinstance(topview_state, dict) else _as_attr(project, "topview_state", {}) or {}
    raw_refs = state.get("xy_reference_by_fixture", {}) if isinstance(state, dict) else {}
    if not isinstance(raw_refs, dict):
        raw_refs = {}
    # ★ 性能(2026-08-05): 未配置任何同步(空 map)时, 全量工程(至冬7.0=11382 台)
    #   逐台扫描是纯浪费(实测 ~163ms/次, 拖动提交链每提交 2 次)——空 map 下每台
    #   cfg 恒为空 → mode=default → 全部跳过, 输出恒为 {} , 直接短路, 逐字节不变。
    if not raw_refs:
        return {}
    global_adjust_deg = _safe_float(state.get("xy_reference_global_angle_deg", 0.0), 0.0) if isinstance(state, dict) else 0.0

    fixture_defs = {
        str(_as_attr(fd, "id", "")): fd
        for fd in (_as_attr(project, "fixture_defs", []) or [])
        if _as_attr(fd, "id", None)
    }

    by_universe: Dict[int, List[XYReference]] = {}
    for fi in (_as_attr(project, "fixtures", []) or []):
        fid = str(_as_attr(fi, "id", ""))
        if not fid:
            continue
        cfg = raw_refs.get(fid, {})
        if not isinstance(cfg, dict) or normalize_reference_mode(cfg.get("mode")) != SYNC_MODE:
            continue

        fd = fixture_defs.get(str(_as_attr(fi, "def_id", "")))
        channels = list(_as_attr(fd, "channels", []) or []) if fd is not None else []
        offsets = _channel_offsets(channels)
        # ★ 尖点 X-only 一对一硬修（2026-08-05 用户实测）：落叶头尖点对（域25
        #   ch464 / 域2 ch331, 全能卡一维 5ch 仅 motor_x）由同步显式加入（cfg
        #   x_only_align）——y 轴自映射（y_addr == x_addr, 变换侧 dy 恒 0），
        #   旋转只写 motor_x。其余无 motor_y 设备不进同步（零变化）。
        if MOTOR_X not in offsets:
            continue
        x_only = MOTOR_Y not in offsets and bool(cfg.get("x_only_align", False))
        if MOTOR_Y not in offsets and not x_only:
            continue

        uni = _safe_int(_as_attr(fi, "universe", 1), 1)
        start = _safe_int(_as_attr(fi, "start_channel", 1), 1) - 1
        x_addr = start + offsets[MOTOR_X]
        y_addr = x_addr if x_only else start + offsets[MOTOR_Y]
        if not (0 <= x_addr < 512 and 0 <= y_addr < 512):
            continue

        x_fine_addr = None
        y_fine_addr = None
        if MOTOR_X_FINE in offsets:
            addr = start + offsets[MOTOR_X_FINE]
            if 0 <= addr < 512:
                x_fine_addr = addr
        if MOTOR_Y_FINE in offsets:
            addr = start + offsets[MOTOR_Y_FINE]
            if 0 <= addr < 512:
                y_fine_addr = addr
        if x_only:
            y_fine_addr = x_fine_addr

        # ★ Z 轴（自转/扇面）扇面基准（2026-08-04）：仅设备 def 含 motor_z 时参与。
        #   角度范围取自导入解析的电机链（MDD Yaw ±180 / 副池三维扇子 Rotation
        #   ±270）；缺省回退 ±180°。扇面目标角：TopView「扇面角度」全局值优先，
        #   其次 cfg 内快照（旧存档），再缺省 0°（水平 +X）。
        z_addr = None
        z_fine_addr = None
        if MOTOR_Z in offsets:
            addr = start + offsets[MOTOR_Z]
            if 0 <= addr < 512:
                z_addr = addr
            if MOTOR_Z_FINE in offsets:
                addr = start + offsets[MOTOR_Z_FINE]
                if 0 <= addr < 512:
                    z_fine_addr = addr
        z_min_deg = DEFAULT_Z_MIN_DEG
        z_max_deg = DEFAULT_Z_MAX_DEG
        if z_addr is not None:
            m_ranges = _as_attr(fd, "motor_ranges", {}) or {}
            rng = m_ranges.get(MOTOR_Z) if isinstance(m_ranges, dict) else None
            if isinstance(rng, (list, tuple)) and len(rng) >= 2:
                lo = _safe_float(rng[0], DEFAULT_Z_MIN_DEG)
                hi = _safe_float(rng[1], DEFAULT_Z_MAX_DEG)
                if hi > lo:
                    z_min_deg, z_max_deg = lo, hi
        # ★ 世界扇面方向 = 扇面角 + 全局角（与 XY angle_deg 同构，2026-08-04
        #   用户「全局角后 Z 跟随」）：TopView「扇面角度」存画布角空间的扇面
        #   目标角（相对角），全局「基准旋转角」是画布级世界旋转 → 两者相加为
        #   世界扇面方向。拖动全局角 Δ → 全组 Z 世界方向同步 +Δ（保持平行）。
        z_angle_deg = _safe_float(
            state.get("z_angle_deg", cfg.get("z_angle_deg", 0.0)), 0.0) \
            + global_adjust_deg

        # ★ 圆排多设备统一化（2026-08-04）：uniform_world_angle 设备的世界目标角
        #   = 偏移 + 全局角（不叠加 base_angle_deg——静止波束 = 安装 + (目标−安装)
        #   = 目标，全部设备同一世界方向；base_angle_deg 仍单独承载安装基准，供
        #   静止电机/扇面基线使用）。非统一设备保持 base + 偏移 + 全局（弧线逐台
        #   切向等现状，零变化）。
        # ★ 2D XY 永走非统一分支（2026-08-10 用户实测最终纠正，复用 3D 同步
        #   代码）：2-Axis Jet（无 motor_z、非 x_only）世界 = 命令 +
        #   安装，同步与三维同构 —— rad = angle = base + θ，base = −真实安装
        #   （topview_panel._xy_install_angle 已取镜像：顶部 ry−180 / 下半弧
        #   −ry）→ 世界 = θ 全组统一（同步基准 = 世界 XY 轴平行方向，超脱圆）。
        #   uniform 分支（rad = base − θ）在此类设备上 = 世界 2·base−θ 逐台
        #   发散（5 轮实测「两把扇子」根因）。三维（含 motor_z → z_addr 非
        #   None）与尖点 X-only（x_only 自映射，落叶头硬修）保持 cfg 原判定，
        #   行为零变化。
        uniform_world = bool(cfg.get("uniform_world_angle", False)) \
            and (z_addr is not None or x_only)
        if "physical_angle_deg" in cfg or "compensation_angle_deg" in cfg:
            base_angle_deg = _safe_float(
                cfg.get("physical_angle_deg", cfg.get("compensation_angle_deg", 0.0)), 0.0)
            angle_deg = (0.0 if uniform_world else base_angle_deg) \
                + _safe_float(cfg.get("angle_offset_deg", 0.0), 0.0) \
                + global_adjust_deg
        elif "base_angle_deg" in cfg or "angle" in cfg:
            base_angle_deg = _safe_float(cfg.get("base_angle_deg", cfg.get("angle", 0.0)), 0.0)
            angle_deg = (0.0 if uniform_world else base_angle_deg) \
                + _safe_float(cfg.get("angle_offset_deg", 0.0), 0.0) \
                + global_adjust_deg
        elif "angle_deg" in cfg:
            # 旧格式：angle_deg 为合成值（含全局角），无独立基准分量——静止
            # 指向退化为「电机方向 = 0」（世界波束 = 安装朝向，不随全局角）
            base_angle_deg = _safe_float(cfg.get("angle_deg"), 0.0)
            angle_deg = base_angle_deg
        elif "angle_offset_deg" in cfg:
            base_angle_deg = 0.0
            angle_deg = _safe_float(cfg.get("angle_offset_deg", 0.0), 0.0) + global_adjust_deg
        else:
            base_angle_deg = 0.0
            angle_deg = global_adjust_deg
        y_axis_sign = -1.0 if _safe_float(cfg.get("y_axis_sign", 1.0), 1.0) < 0.0 else 1.0
        # ★ 2D XY 设备（2026-08-10 用户实测最终纠正）：base 承载真实安装朝向
        #   （2-Axis Jet = fmap FixtureNode.rot_y，顶部弧线组合旋转 RotX=180&
        #   RotZ=180 → 世界偏航 = 180 − rot_y，见 topview_panel._xy_install_angle），
        #   变换侧统一走 rad = base − angle（下方 _apply_xy_reference_rotation 所有
        #   uniform 设备同公式，无 z_addr 特判）——同步 = 命令补偿安装（命令 =
        #   θ − base），世界 = 安装 + 命令 = θ 全组统一。ca2c298 曾把 2D XY rad
        #   改为 −angle（命令=θ 直通）= 恒等变换 =「点了和没点一样」被用户当场
        #   识破回退；唯一真根因是 base ≠ 真实安装朝向，而非公式分叉。

        # ★ 静态旋转参数预计算（2026-08-12 候选点#12）：与变换侧公式逐字同式
        #   （uniform: rad=base−angle / 否则 rad=angle），cos/sin/mode/y_sign
        #   纯函数于 frozen 字段 → build 期一次，每 tick 零重算；None 回退旧路径。
        # ★ 非有限角度守卫（2026-08-12）：cfg 角度可携带 NaN/±inf（_safe_float
        #   原样放行），math.cos(radians(±inf)) 抛 ValueError → build 期崩溃
        #   （旧代码 cos/sin 在 tick 期同样崩溃，行为一致）——角度任一非有限
        #   → 4 缓存字段全 None，变换侧回退旧公式。
        _ang_finite = math.isfinite(float(base_angle_deg or 0.0)) \
            and math.isfinite(float(angle_deg or 0.0))
        if uniform_world:
            _rad = math.radians(float(base_angle_deg or 0.0) - float(angle_deg or 0.0))
        else:
            _rad = math.radians(float(angle_deg or 0.0))
        if _ang_finite:
            _mode_is_sync = normalize_reference_mode(SYNC_MODE) == SYNC_MODE
            _cos_rad = math.cos(_rad)
            _sin_rad = math.sin(_rad)
            _y_sign_eff = y_axis_sign
        else:
            _mode_is_sync = None
            _cos_rad = None
            _sin_rad = None
            _y_sign_eff = None

        ref = XYReference(
            fixture_id=fid,
            universe=uni,
            x_addr=x_addr,
            y_addr=y_addr,
            x_fine_addr=x_fine_addr,
            y_fine_addr=y_fine_addr,
            angle_deg=angle_deg,
            base_angle_deg=base_angle_deg,
            y_axis_sign=y_axis_sign,
            mode=SYNC_MODE,
            z_addr=z_addr,
            z_fine_addr=z_fine_addr,
            z_min_deg=z_min_deg,
            z_max_deg=z_max_deg,
            z_angle_deg=z_angle_deg,
            uniform_world=uniform_world,
            mode_is_sync=_mode_is_sync,
            cos_rad=_cos_rad,
            sin_rad=_sin_rad,
            y_sign_eff=_y_sign_eff,
        )
        by_universe.setdefault(uni, []).append(ref)
    return by_universe


def _read_axis_logical(data: np.ndarray, coarse_addr: int, fine_addr: Optional[int]) -> float:
    coarse = int(data[int(coarse_addr)])
    if fine_addr is None:
        return float(coarse)
    fine = int(data[int(fine_addr)])
    return float((coarse << 8) + fine) / 256.0


def _logical_to_16bit_pair(value: float) -> tuple[int, int]:
    logical = max(0.0, min(255.0, float(value)))
    if logical >= 255.0:
        v16 = 65535
    else:
        v16 = int(round(logical * 256.0))
    v16 = max(0, min(65535, v16))
    return (v16 >> 8) & 0xFF, v16 & 0xFF


def _write_axis_logical(data: np.ndarray, coarse_addr: int, fine_addr: Optional[int], value: float) -> None:
    logical = max(0.0, min(255.0, float(value)))
    if fine_addr is None:
        data[int(coarse_addr)] = max(0, min(255, int(round(logical))))
        return
    coarse, fine = _logical_to_16bit_pair(logical)
    data[int(coarse_addr)] = coarse
    data[int(fine_addr)] = fine


def read_axis_logical_value(data: np.ndarray, coarse_addr: int, fine_addr: Optional[int] = None) -> float:
    """Read an 8-bit logical motor axis, combining fine if present."""
    return _read_axis_logical(data, coarse_addr, fine_addr)


def logical_to_16bit_pair(value: float) -> tuple[int, int]:
    """Convert a logical 0..255 motor value to coarse/fine bytes."""
    return _logical_to_16bit_pair(value)


def write_axis_logical_value(
    data: np.ndarray,
    coarse_addr: int,
    fine_addr: Optional[int] = None,
    value: float = LOGICAL_CENTER,
) -> None:
    """Write an 8-bit logical motor axis, expanding to coarse/fine if present."""
    _write_axis_logical(data, coarse_addr, fine_addr, value)


def _write_z_angle(out: np.ndarray, ref: XYReference, world_angle_deg: float) -> None:
    """Z 轴（自转/扇面）绝对写值：电机角 = 世界扇面目标角 − 安装基准角。

    与 XY 静止指向同构（世界方向 = 安装朝向 + 电机角）：世界扇面方向 = RotY
    （≈基准角 base_angle_deg）+ (world_angle_deg − base_angle_deg) =
    world_angle_deg，全组设备同向 →「Z 扇面全部水平平行」（2026-08-04 用户
    需求，默认 0°=水平+X）。world_angle_deg = ref.z_angle_deg（扇面角+全局角）
    → 全局角变化时全组 Z 世界方向同步跟随；world_angle_deg = Z 组命令角
    （Programmer Z 滑块广播的统一目标角）→ 全部同步设备世界扇面同角
    （2026-08-04 用户实测「拖 Z 滑块扇面分块」修复，每台按自己范围映射 DMX）。
    DMX = 电机角在设备 Z 范围 [min,max]（MDD ±180 / 扇子 ±270）内线性映射：
    DMXHomeValue=128≈0° 与实测端点吻合。
    """
    motor_angle = float(world_angle_deg or 0.0) - (ref.base_angle_deg or 0.0)
    z_min = float(ref.z_min_deg if ref.z_min_deg is not None else DEFAULT_Z_MIN_DEG)
    z_max = float(ref.z_max_deg if ref.z_max_deg is not None else DEFAULT_Z_MAX_DEG)
    motor_angle = _reachable_z_equivalent(motor_angle, ref.base_angle_deg, z_min, z_max)
    span = max(1e-9, z_max - z_min)
    logical = max(0.0, min(255.0, ((motor_angle - z_min) / span) * 255.0))
    _write_axis_logical(out, ref.z_addr, ref.z_fine_addr, logical)


def _reachable_z_equivalent(angle, base, minimum, maximum):
    """Preserve valid angles; use a reachable whole-turn equivalent if needed."""
    # Noncanonical legacy baselines retain the existing endpoint safety clamp.
    if not -180.0 <= float(base or 0.0) <= 180.0:
        return angle
    if not all(math.isfinite(v) for v in (angle, minimum, maximum)) or minimum > maximum:
        return angle
    if minimum <= angle <= maximum:
        return angle
    first = math.ceil((minimum - angle) / 360.0)
    last = math.floor((maximum - angle) / 360.0)
    return angle + 360.0 * max(first, min(0, last)) if first <= last else angle


def _write_z_baseline(out: np.ndarray, ref: XYReference) -> None:
    """Z 轴扇面基准（薄封装）：目标角 = ref.z_angle_deg（扇面角 + 全局角）。"""
    _write_z_angle(out, ref, float(ref.z_angle_deg or 0.0))


def logical_z_to_world_angle(logical: float) -> float:
    """Programmer Z 滑块逻辑值（0-255，128=中心）→ 世界扇面目标角（-180°~+180°）。

    约定与 TopView「扇面角度」控件同空间（画布角，0=水平+X）：128 = 0°，
    全量程 ±180°（与 DEFAULT_Z_MIN/MAX_DEG 一致）。同步组命令用统一角 →
    每台按自己 Z 范围映射 DMX → 全部同步设备世界扇面同角（2026-08-04 用户
    需求「拖动 Z 滑块 = 临时/实时扇面角，世界坐标同角度」）。
    """
    return (float(logical) - LOGICAL_CENTER) * (180.0 / 128.0)


def _z_command_applies(ref: XYReference, z_ids: Optional[set],
                       clip_owned_z: Optional[set] = None) -> bool:
    """Z 组命令是否作用于该设备（z_ids=None = 全组，旧语义）。

    ★★ 2026-09-18 用户实报（仅选中内圈 12 台，全部扇子 Z 仍在动）**根因**：
      组命令是**单一全局标量**，物理侧无差别套用到全部 sync ref → 只要
      Programmer/摇杆动过任何 Z 控件，**全组**设备的 Z 都被覆写成同一世界角，
      与本次实际写入的（选中的）设备集无关。修法：命令随身携带**本次写入的
      设备 id 集**，只有集合内设备吃命令，集合外设备回退"无命令"语义
      （Z 静止写扇面基准 / 有 clip 运动不覆盖）→ 未被选中的设备纹丝不动。

    ★★ 2026-09-20 用户实报（"一旦有一个 clip 是同步模式，后续的就算选默认都会
      一直强行同步"）**根因**：组命令是**会话级**状态（不随工程保存、也无人
      清理），而 clip 的录制值才是"那一条 clip 自己的状态"。做过一次同步的 clip
      留下的命令会**永久**压掉后续任何 clip 录下的 Z。修法：**clip 正在驱动该
      设备的 Z 时，clip 值优先** —— `clip_owned_z` = 本帧由 clip 接管的设备 id
      集，命令对它们不生效。
    """
    fid = str(getattr(ref, "fixture_id", "") or "")
    if z_ids is not None and fid not in z_ids:
        return False
    if clip_owned_z and fid in clip_owned_z:
        return False
    return True


def _apply_z_output(out: np.ndarray, ref: XYReference, to_virtual: bool,
                    z_command_deg: Optional[float] = None,
                    z_ids: Optional[set] = None, *,
                    clip_owned_z: Optional[set] = None) -> None:
    """物理输出侧 Z 轴最终写值（归位/偏离两分支共用）：Z 组命令优先，
    否则 Z 静止时写扇面基准。

    - z_command_deg 非 None（同步组命令：Programmer 同步模式下拖 Z 滑块/摇杆
      Z 手动值广播而来）→ 按统一世界角写同步设备，每台按自己 [min,max] 范围
      映射 DMX → 同步设备世界扇面同角（用户需求 2026-08-04）。
      组命令是广播语义——即使当前 Z 有 clip 运动也以手动命令为准（手动
      override 优先）。**作用范围由 z_ids 限定**（None = 全组旧语义；
      非 None = 仅本次实际写入的设备，见 _z_command_applies）。
    - 无命令（含命令不作用于本设备）→ Z 静止（中心 128 或默认 0）时写扇面
      基准（扇面角度映射）——包括 XY 归中（128）/0 帧：Z 扇面在同步模式下恒
      按扇面角度输出，不随 XY 归中恢复（2026-08-04 用户实测修正）；Z 有 clip
      运动（非中心/非 0）时不动它（运动不覆盖，效果/编舞动画不受影响）。
    仅物理输出侧生效（to_virtual 逆变换侧保持原样）。无 motor_z 的设备
    z_addr=None → 恒 no-op（向后兼容）。
    """
    if to_virtual or ref.z_addr is None:
        return
    if z_command_deg is not None and _z_command_applies(ref, z_ids, clip_owned_z):
        _write_z_angle(out, ref, float(z_command_deg))
        return
    z_cur = _read_axis_logical(out, ref.z_addr, ref.z_fine_addr)
    if abs(z_cur - LOGICAL_CENTER) < 1e-9 or int(z_cur) == 0:
        _write_z_baseline(out, ref)


def _ref_rotation_cos_sin(ref: XYReference) -> tuple[float, float]:
    """回退路径（2026-08-12 候选点#12）：手动构造/无缓存字段 ref 的旋转角
    cos/sin —— 旧逐 tick 公式原样搬移，build_xy_reference_map 预计算后正常
    ref 不再进入本函数（None 回退），逐字节零变化。
    """
    if getattr(ref, "uniform_world", False):
        # ★ 圆排多设备统一化（2026-08-04 语义）：内容旋转参数 = 安装基准 −
        #   世界目标（base − angle）。物理波束 = 安装朝向 + 内容角 − 参数 →
        #   安装抵消，波束 = 世界偏移——推 X/Y 全部设备世界一致。
        #   ★ 本分支仅三维扇子（含 motor_z）与尖点 X-only 可达：2D XY 由
        #   build_xy_reference_map 强制排除（无 motor_z 且非 x_only →
        #   uniform_world=False）。历史根因：rad = base−θ 在 2-Axis Jet
        #   （世界 = 命令本身，不叠加安装）上 = 世界 2·base−θ 逐台发散 =
        #   用户实测「两把扇子一开一合」（5 轮）。
        rad = math.radians(
            float(ref.base_angle_deg or 0.0) - float(ref.angle_deg or 0.0))
    else:
        # ★ 2D XY 同步唯一路径（2026-08-10 用户「三维只多一个 Z 轴，
        #   二维 XY 与三维 XY 同步效果一致」——复用三维同步代码）：
        #   rad = angle = base + θ，base 已由 topview_panel._xy_install_
        #   angle 取 −真实安装（顶部 ry−180 / 下半弧 −ry）→ 世界 = θ
        #   全组统一（同步基准 = 世界 XY 轴平行方向，超脱圆），与默认
        #   模式（世界 = 安装 + θ 逐台不同）有真实区别。三维扇子同
        #   公式（切线 base = −安装，世界 = θ，从没错）。
        rad = math.radians(float(ref.angle_deg or 0.0))
    return math.cos(rad), math.sin(rad)


def _apply_xy_reference_rotation(
    data: np.ndarray,
    refs_by_universe: Dict[int, List[XYReference]],
    universe: int,
    *, to_virtual: bool,
    z_command_deg: Optional[float] = None, z_ids: Optional[set] = None,
    clip_owned_z: Optional[set] = None,
) -> np.ndarray:
    refs = refs_by_universe.get(int(universe)) if refs_by_universe else None
    if not refs:
        return data

    out = np.asarray(data, dtype=np.uint8).copy()
    for ref in refs:
        # ★ 缓存优先（2026-08-12 候选点#12）：build_xy_reference_map 预计算
        #   mode_is_sync；None（手动构造 ref）→ 回退旧归一化判定，逐字节零变化。
        if ref.mode_is_sync is not None:
            if not ref.mode_is_sync:
                continue
        elif normalize_reference_mode(ref.mode) != SYNC_MODE:
            continue
        x = _read_axis_logical(out, ref.x_addr, ref.x_fine_addr)
        # ★ 尖点 X-only 一对一（2026-08-05）：y 自映射（x_addr==y_addr）= 单轴
        #   设备（全能卡一维 5ch 仅 motor_x）——无独立 Y 轴, dy 恒 0, 输出只写
        #   motor_x（旋转 X 分量）。
        y = (LOGICAL_CENTER if ref.x_addr == ref.y_addr
             else _read_axis_logical(out, ref.y_addr, ref.y_fine_addr))
        dx = x - LOGICAL_CENTER
        dy = y - LOGICAL_CENTER
        # ★ 静态缓存优先（2026-08-12 候选点#12）：build_xy_reference_map 已
        #   按同式预计算 cos/sin；None（手动构造 ref）→ 回退 _ref_rotation_
        #   cos_sin 旧公式，逐字节零变化。
        if ref.cos_rad is not None and ref.sin_rad is not None:
            c = ref.cos_rad
            s = ref.sin_rad
        else:
            # ★ 2026-08-13 EDGE-07: 非有限角守卫 —— 修复前 build 期守卫
            #   (9a0b3d9) 只置 4 缓存字段 None, ref 仍注册进 by_universe,
            #   每 tick 落到本回退对同一 ±inf 角重算 math.cos → 每 tick
            #   ValueError + log 刷屏 + 该宇宙变换静默失效; NaN 经 min/max
            #   钳成 255 满偏静默写偏。非有限 ref 直接跳过本设备(transform
            #   侧 continue, 其余设备 ref 照常)。
            ba = float(getattr(ref, "base_angle_deg", 0.0) or 0.0)
            aa = float(getattr(ref, "angle_deg", 0.0) or 0.0)
            if not (math.isfinite(ba) and math.isfinite(aa)):
                continue
            c, s = _ref_rotation_cos_sin(ref)
        if ref.y_sign_eff is not None:
            y_sign = ref.y_sign_eff
        else:
            y_sign = -1.0 if float(getattr(ref, "y_axis_sign", 1.0) or 1.0) < 0.0 else 1.0
        # ★ 静止判定（2026-08-04 修复，落叶头静止基准偏）：16bit 电机对的精细字节
        #   残留使「精确相等」判空失败——(128,128) 合成逻辑 128.5 ≠ 128 → 落入
        #   下方相对旋转分支 → 微小量化残差被旋转出任意方向（落叶头 73/73 静止
        #   世界方向全变 45° 实测）。改半径判定：距中心 <1 逻辑单位（半个 8bit
        #   步进）视为静止，覆盖 (128,128)=0.707、(128,1)、fractional 插值、
        #   (127,255)=0.996 等中心量化噪声；真实推杆/运动值 ≥1 单位不误伤。
        #   8bit 设备无 fine 通道，精确 128 恒命中，行为零变化。
        #   ★ 零帧同归位（2026-08-05 用户硬要求延续）：未播放/无层写入的全 0 帧
        #   也是静止态——旧实现（2026-08-03「同步没效果」修复）把 0 帧视为中心
        #   写静止绝对指向，用户实测「一同步 128 就整体往右偏」（底弧线 120 台
        #   全组 x=168）即源于此。归位恒 128，同步仅作用于「偏离」（非 128）
        #   帧的方向映射，与中心归位分支一致。
        # x_only 自映射设备无独立 y 轴（y 恒 128），全 0 帧同视为零帧静止态
        is_zero_frame = ((int(x) == 0) and (int(y) == 0 or ref.x_addr == ref.y_addr))
        if math.hypot(dx, dy) < 1.0 or is_zero_frame:
            if not to_virtual:
                # ★ 归位恒等（2026-08-05 用户硬要求）：输入 128（摇杆归中立位/无
                #   偏离）或全 0（未播放/无层写入）时输出恒 128——不做静止绝对
                #   指向、不做扇面基准偏置。历史 810e94d 静止绝对指向
                #   （128±40·(cos β−α)）把归位抬离 128：底弧线 120 台全组 x=168
                #   整体右移、扇子 10 台逐台偏转（用户实测「一同步 128 就整体
                #   往右偏」）。归位只允许恒 128，同步仅作用于「偏离」（非 128）
                #   时的方向映射。
                _write_axis_logical(out, ref.x_addr, ref.x_fine_addr, LOGICAL_CENTER)
                if ref.x_addr != ref.y_addr:
                    _write_axis_logical(out, ref.y_addr, ref.y_fine_addr, LOGICAL_CENTER)
                # ★ Z 扇面不随 XY 归中恢复（2026-08-04 用户实测修正）：与偏离
                #   分支共用 _apply_z_output——XY 归中（128）或 0 帧时 Z 仍写扇面
                #   基准（扇面角度映射，非设备归位角 128）：同步模式下 Z 扇面恒
                #   按扇面角度输出，与 XY 偏离无关；z_command_deg（Z 滑块/摇杆
                #   Z 手动命令）优先；Z 有 clip 运动（非中心/非 0）不覆盖。
                #   仅物理输出侧生效；虚拟逆变换侧保持原样，避免 clip 预变换
                #   基线被抬升。
                _apply_z_output(out, ref, to_virtual, z_command_deg, z_ids,
                                 clip_owned_z=clip_owned_z)
            continue
        if to_virtual:
            next_x = dx * c - dy * y_sign * s
            next_y = dx * s + dy * y_sign * c
        else:
            next_x = dx * c + dy * s
            next_y = y_sign * (-dx * s + dy * c)
        _write_axis_logical(out, ref.x_addr, ref.x_fine_addr, LOGICAL_CENTER + next_x)
        if ref.x_addr != ref.y_addr:
            _write_axis_logical(out, ref.y_addr, ref.y_fine_addr, LOGICAL_CENTER + next_y)
        _apply_z_output(out, ref, to_virtual, z_command_deg, z_ids,
                                 clip_owned_z=clip_owned_z)
    return out


def apply_xy_reference_to_physical(
    data: np.ndarray,
    refs_by_universe: Dict[int, List[XYReference]],
    universe: int,
    z_command_deg: Optional[float] = None, z_ids: Optional[set] = None,
    *, clip_owned_z: Optional[set] = None,
) -> np.ndarray:
    """Map virtual TopView XY axes to the fixture's physical DMX axes.

    z_command_deg: 同步组 Z 命令（统一世界扇面目标角，-180°~+180°）。非 None
    时按统一世界角写同步设备（每台按自己 Z 范围映射 DMX → 世界扇面同角）；
    None = 现状语义（Z 静止写基准 / 运动不覆盖）。默认 None → 旧调用逐字节兼容。
    z_ids: 命令**作用范围**（本次实际写入的设备 id 集，None = 全组旧语义）。
    """
    return _apply_xy_reference_rotation(data, refs_by_universe, universe,
                                        to_virtual=False, z_command_deg=z_command_deg,
                                        z_ids=z_ids, clip_owned_z=clip_owned_z)


def apply_xy_reference_to_virtual(
    data: np.ndarray,
    refs_by_universe: Dict[int, List[XYReference]],
    universe: int,
) -> np.ndarray:
    """Map physical DMX axes back to TopView's virtual XY axes."""
    return _apply_xy_reference_rotation(data, refs_by_universe, universe, to_virtual=True)


def apply_xy_reference_transform(
    data: np.ndarray,
    refs_by_universe: Dict[int, List[XYReference]],
    universe: int,
) -> np.ndarray:
    """Backward-compatible alias for virtual-to-physical output mapping."""
    return apply_xy_reference_to_physical(data, refs_by_universe, universe)
