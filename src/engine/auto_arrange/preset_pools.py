"""
preset_pools — 自动编舞的"真 effect 手法池"(L2.5)

把真实 effect 库(EffectEngine,1611 preset)按声部类归成六个"手法池",供编舞按
段落/能量/seed 多样化选择 preset_id,经 voice_clip_renderer 的 real_effect 分支调
真引擎渲染——取代自制 8 形状,变化丰富度提升一个量级。

每池 = 骨架(NAMED 干净手柄,权重靠前) + 填充(海量 cur_*/vN 池,按能量分桶)。
启动时构建一次缓存。纯逻辑(惰性 import 引擎,避开 core→ui 方向硬依赖)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 声部类(与 voice_model 同口径)
VC_MOTOR = "motor"
VC_PUMP = "pump"
VC_VALVE = "valve"
VC_FOUNTAIN_RGB = "fountain_rgb"
VC_MATRIX_RGB = "matrix_rgb"
VC_DIMMER = "dimmer"

# ── 各声部类的 NAMED 骨架(语义干净、可控,优先选)──────────────────────────
# 已验证存在(.codex_named_check);缺失的会在构建时自动跳过。
_SKELETON: Dict[str, List[str]] = {
    VC_MOTOR: [
        "cur_ms_spirals1", "motor_scan_wall", "motor_chase_target", "motor_random_jump",
        "joystick_circle", "joystick_figure8", "joystick_wave_scan",
        "motor_lissajous_v25", "cur_motor2_star_s95",
        "motor_dual_dragon", "motor_seagull_swing", "motor_willow_swing",
    ],
    VC_PUMP: [
        "pump_pressure_wave", "wave_sine", "wave_cosine", "wave_square",
        "dynamic_crescendo", "heartbeat", "fountain_burst", "fountain_running",
        "fountain_height_gradient", "fountain_tidal",
    ],
    VC_VALVE: [
        "valve_pulse", "valve_sequential", "strobe_duty", "strobe_ramp",
        "dimmer_pulse", "bounce_ball",
    ],
    VC_FOUNTAIN_RGB: [
        "color_aurora", "color_ocean", "color_fire", "cur_color_rainbow_s22",
        "wave_rainbow", "color_hsv_scan", "smooth_breathe", "color_complementary",
        "cur_color2_dualtone_s81", "light_color_chase",
    ],
    VC_MATRIX_RGB: [
        "matrix_scroll_h", "matrix_scroll_v", "matrix_spiral", "matrix_explode",
        "matrix_scan_row", "matrix_scan_col", "matrix_gradient", "matrix_alternate",
        "matrix_blink",
    ],
    VC_DIMMER: [
        "dimmer_pulse", "fader_breath", "fader_curve", "smooth_breathe",
        "staircase", "dimmer_random_jump", "dimmer_group_alt", "light_skip_chase",
    ],
}

# 通用兜底池(任意声部缺池时取)
_UNIVERSAL_FALLBACK = ["wave_sine", "chase_single", "smooth_breathe", "dimmer_pulse"]

# 声部类 → 该类必须能驱动的通道(用于校验 preset.target_channels 相容)
_VC_CHANNELS = {
    VC_MOTOR: {"motor_x", "motor_y"},
    VC_PUMP: {"pump"},
    VC_VALVE: {"valve"},
    VC_FOUNTAIN_RGB: {"R", "G", "B"},
    VC_MATRIX_RGB: {"R", "G", "B"},
    VC_DIMMER: {"dimmer"},
}


def _is_audio_preset(p) -> bool:
    cat = str(getattr(p, "category", "") or "")
    if "音频" in cat or "audio" in cat.lower():
        return True
    pid = str(getattr(p, "id", "") or "").lower()
    if "audio" in pid:
        return True
    tags = [str(t).lower() for t in (getattr(p, "tags", []) or [])]
    return any("audio" in t for t in tags)


class PresetPools:
    """六声部类手法池 + 按能量档加权选择。"""

    def __init__(self, pools: Dict[str, List[str]], all_ids: set):
        self.pools = pools                 # vc → [preset_id...](骨架在前,填充在后)
        self.all_ids = all_ids
        # 记录每池骨架数(选择时骨架优先)
        self._skel_n = {vc: len([i for i in ids if i in set(_SKELETON.get(vc, []))])
                        for vc, ids in pools.items()}

    def pool_for(self, vc: str) -> List[str]:
        return self.pools.get(vc) or list(_UNIVERSAL_FALLBACK)

    def has(self, preset_id: str) -> bool:
        return preset_id in self.all_ids

    def skeleton_count(self, vc: str) -> int:
        return self._skel_n.get(vc, 0)


_SINGLETON: Optional[PresetPools] = None


def get_preset_pools() -> PresetPools:
    """构建/返回六声部手法池(进程内缓存)。引擎不可用时返回骨架兜底池。"""
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    all_ids: set = set()
    pools: Dict[str, List[str]] = {vc: [] for vc in _SKELETON}
    try:
        from ui.effect_engine import get_engine
    except ImportError:
        try:
            from src.ui.effect_engine import get_engine
        except ImportError:
            get_engine = None
    presets = []
    if get_engine is not None:
        try:
            eng = get_engine()
            presets = [p for p in eng.all_presets() if not _is_audio_preset(p)]
            all_ids = set(p.id for p in presets)
        except Exception:
            presets = []
    by_id = {p.id: p for p in presets}
    for vc, skel in _SKELETON.items():
        ids: List[str] = []
        # 1) 骨架(存在的,保序)
        for pid in skel:
            if pid in by_id and pid not in ids:
                ids.append(pid)
        # 2) 填充:target_channels 相容该声部 的其余 preset(去重,限量防爆)
        need = _VC_CHANNELS.get(vc, set())
        fillers = []
        for p in presets:
            if p.id in ids:
                continue
            tc = set(p.target_channels or [])
            if need and not (need <= tc):
                continue
            fillers.append(p.id)
        # 填充按 id 排序(确定性),限 200 个/池(够多样,避免选择空间过大)
        fillers.sort()
        ids.extend(fillers[:200])
        pools[vc] = ids if ids else list(_UNIVERSAL_FALLBACK)
    _SINGLETON = PresetPools(pools, all_ids)
    return _SINGLETON
