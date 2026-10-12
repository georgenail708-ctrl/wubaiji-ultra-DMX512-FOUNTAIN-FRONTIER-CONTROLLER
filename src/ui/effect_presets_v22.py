"""
effect_presets_v22.py — DMXTimeline 效果预设 v22 大幅扩展
========================================================
新增 80+ 专业预设，覆盖：
  - 喷泉专用水型（直喷渐变/摇摆/扇形/花篮/编网/瀑布等）
  - 高级动态效果（对称展开/螺旋上升/涟漪扩散/呼吸潮汐等）
  - MA 控台风格（Phaser/Fan/Delay 链）
  - 流彩（Wave-FX/Position-FX/Color-FX）
  - 音乐同步（重音触发/渐强渐弱/力度映射/BPM 分频）
  - 矩阵图案（棋盘翻转/生命游戏/粒子系统/文字滚动）
  - 位置轨迹（利萨如/心形/星形/螺旋等）
所有预设利用 TopView 坐标实现空间感知排序。
"""
from __future__ import annotations
import math
import random
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec

def _register_v22_presets(engine: "EffectEngine"):
    """在 EffectEngine 实例上注册 v22 扩展预设"""
    from .effect_engine import EffectPreset, ParamSpec, WAVE_FUNCS
    reg = engine._reg

    # ═══════════════════════════════════════════════════════════════════════
    #  辅助工具
    # ═══════════════════════════════════════════════════════════════════════
    def _clamp(v, lo=0, hi=255):
        return max(lo, min(hi, int(v)))

    def _ease_in_out(t):
        """Hermite S 曲线"""
        return t * t * (3.0 - 2.0 * t)

    def _ease_in_cubic(t):
        return t * t * t

    def _ease_out_cubic(t):
        return 1.0 - (1.0 - t) ** 3

    def _ease_in_out_cubic(t):
        if t < 0.5:
            return 4 * t * t * t
        else:
            return 1 - (-2 * t + 2) ** 3 / 2

    def _smoothstep(edge0, edge1, x):
        t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0))) if edge1 != edge0 else 0.0
        return t * t * (3.0 - 2.0 * t)

    def _dist(x1, y1, x2, y2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

    def _get_pos(fi, sorted_fx, positions_cache={}):
        """获取设备的 TopView 坐标"""
        # 注意：positions 通过闭包传入
        return (0.0, 0.0)  # 占位，实际在 compute 中由 sorted_fx 提供

    # ═══════════════════════════════════════════════════════════════════════
    #  A. 喷泉专用水型预设
    # ═══════════════════════════════════════════════════════════════════════

    # A1: 直喷渐变（从左到右高度渐变）
    def _fountain_height_gradient(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "valve")
        direction = params.get("direction", "左→右")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            if direction == "右→左":
                ratio = 1.0 - ratio
            elif direction == "中→外":
                ratio = abs(2.0 * ratio - 1.0)
            elif direction == "外→中":
                ratio = 1.0 - abs(2.0 * ratio - 1.0)
            val = _clamp(lo + ratio * (hi - lo))
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="fountain_height_gradient", name="直喷渐变", category="Fountain",
        description="喷泉高度从一端到另一端线性渐变，支持多种方向",
        target_channels=["valve", "pump", "dimmer"], icon="📐",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("lo", "最低值", "int", 30, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "最高值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("direction", "方向", "choice", "左→右",
                      choices=["左→右", "右→左", "中→外", "外→中"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ],
    ), _fountain_height_gradient)

    # A2: 摇摆扫描
    def _fountain_swing_scan(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        width = float(params.get("width", 0.3))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        center = (math.sin(t * speed * 2 * math.pi) + 1) / 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            dist = abs(pos - center)
            if dist < width / 2:
                brightness = 1.0 - (dist / (width / 2))
            else:
                brightness = 0.0
            val = _clamp(lo + brightness * (hi - lo))
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="fountain_swing_scan", name="摇摆扫描", category="Fountain",
        description="光带/水柱在设备阵列上来回扫描，模拟摇摆喷头效果",
        target_channels=["valve", "pump", "dimmer"], icon="🔄",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width", "光带宽度", "float", 0.3, 0.05, 1.0, 0.05,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _fountain_swing_scan)

    # A3: 扇形展开
    def _fountain_fan_spread(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        phase = (t * speed) % 1.0
        open_count = int(phase * n)
        mid = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist_from_mid = abs(seq_i - mid)
            if dist_from_mid <= open_count // 2:
                ef.values[orig_i][ch] = hi
            else:
                ef.values[orig_i][ch] = lo
    reg(EffectPreset(
        id="fountain_fan_spread", name="扇形展开", category="Fountain",
        description="从中心向两侧逐步展开，模拟扇形喷泉开启",
        target_channels=["valve", "pump", "dimmer"], icon="🌊",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "展开速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("hi", "开启值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "关闭值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_fan_spread)

    # A4: 花篮效果
    def _fountain_basket(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 80))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            # 抛物线形状 + 时间变化
            height = 1.0 - 4.0 * (ratio - 0.5) ** 2
            pulse = (math.sin(t * speed * 2 * math.pi + ratio * math.pi) + 1) / 2
            val = _clamp(lo + height * pulse * (hi - lo))
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="fountain_basket", name="花篮效果", category="Fountain",
        description="中间高两侧低的抛物线形状，带呼吸脉动",
        target_channels=["valve", "pump", "dimmer"], icon="🧺",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "脉动速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 80, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_basket)

    # A5: 编网效果（交叉编织）
    def _fountain_weaving(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            # 奇偶交替 + 相位偏移
            if seq_i % 2 == 0:
                v = (math.sin(t * speed * 2 * math.pi + ratio * 4 * math.pi) + 1) / 2
            else:
                v = (math.sin(t * speed * 2 * math.pi - ratio * 4 * math.pi + math.pi) + 1) / 2
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
    reg(EffectPreset(
        id="fountain_weaving", name="编网效果", category="Fountain",
        description="奇偶设备反向运动形成交叉编织图案",
        target_channels=["valve", "pump", "dimmer"], icon="🕸️",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_weaving)

    # A6: 瀑布下落
    def _fountain_waterfall(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        tail = float(params.get("tail", 0.4))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        head = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos = seq_i / max(1, n - 1)
            dist = (head - pos) % 1.0
            if dist < tail:
                brightness = 1.0 - dist / tail
            else:
                brightness = 0.0
            ef.values[orig_i][ch] = _clamp(lo + brightness * (hi - lo))
    reg(EffectPreset(
        id="fountain_waterfall", name="瀑布下落", category="Fountain",
        description="带拖尾的单向流动效果，模拟水流下落",
        target_channels=["valve", "pump", "dimmer"], icon="🌊",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "流速", "float", 2.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tail", "拖尾长度", "float", 0.4, 0.05, 1.0, 0.05,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_waterfall)

    # A7: 涟漪扩散（从中心向外）
    def _fountain_ripple(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        rings = int(params.get("rings", 3))
        decay = float(params.get("decay", 0.7))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - n // 2) / max(1, n // 2)
            wave = 0.0
            for ring in range(rings):
                ring_pos = (t * speed - ring * 0.3) % 1.0
                ring_dist = abs(dist - ring_pos)
                if ring_dist < 0.1:
                    wave += (1.0 - ring_dist / 0.1) * (decay ** ring)
            ef.values[orig_i][ch] = _clamp(wave * hi)
    reg(EffectPreset(
        id="fountain_ripple", name="涟漪扩散", category="Fountain",
        description="从中心向外扩散的多层涟漪效果",
        target_channels=["valve", "pump", "dimmer"], icon="◎",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "扩散速度", "float", 1.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("rings", "涟漪层数", "int", 3, 1, 8, 1,
                tooltip="控制环形层数。值越大同心圈层越多"),
            ParamSpec("decay", "衰减系数", "float", 0.7, 0.1, 1.0, 0.05,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _fountain_ripple)

    # A8: 喷泉呼吸潮汐
    def _fountain_tidal(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "valve")
        easing = params.get("easing", "S曲线")
        n = len(sorted_fx)
        raw = (math.sin(t * speed * 2 * math.pi) + 1) / 2
        if easing == "S曲线":
            raw = _ease_in_out(raw)
        elif easing == "缓入":
            raw = _ease_in_cubic(raw)
        elif easing == "缓出":
            raw = _ease_out_cubic(raw)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase_offset = seq_i / max(1, n) * 0.2
            local_raw = (math.sin((t + phase_offset) * speed * 2 * math.pi) + 1) / 2
            if easing == "S曲线":
                local_raw = _ease_in_out(local_raw)
            ef.values[orig_i][ch] = _clamp(lo + local_raw * (hi - lo))
    reg(EffectPreset(
        id="fountain_tidal", name="呼吸潮汐", category="Fountain",
        description="整体缓慢呼吸起伏，带微小相位差产生潮汐感",
        target_channels=["valve", "pump", "dimmer"], icon="🌬️",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "呼吸频率", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "底值", "int", 30, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("easing", "缓动", "choice", "S曲线", choices=["线性", "S曲线", "缓入", "缓出"],
                tooltip="选择缓动函数类型。控制变化速度的加速/减速节奏"),
        ],
    ), _fountain_tidal)

    # A9: 跑泉（顺序追逐，带可调间距）
    def _fountain_running(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        gap = int(params.get("gap", 1))
        on_count = int(params.get("on_count", 2))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        period = on_count + gap
        offset = int(t * speed) % period
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pos_in_period = (seq_i + offset) % period
            if pos_in_period < on_count:
                ef.values[orig_i][ch] = hi
            else:
                ef.values[orig_i][ch] = lo
    reg(EffectPreset(
        id="fountain_running", name="跑泉", category="Fountain",
        description="经典跑泉效果：一组设备顺序点亮并移动",
        target_channels=["valve", "pump", "dimmer"], icon="🏃",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "移动速度", "float", 3.0, 0.5, 20.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("on_count", "同时亮起数", "int", 2, 1, 10, 1,
                tooltip="控制同时开启的设备数量。值越大单次亮起的设备越多"),
            ParamSpec("gap", "间隔数", "int", 1, 0, 10, 1,
                tooltip="控制设备/元素之间的间隔。值越大间距越宽，视觉上越分散"),
            ParamSpec("hi", "开启值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "关闭值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_running)

    # A10: 对称展开（从中心同时向两侧展开）
    def _fountain_symmetric_spread(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        tail = float(params.get("tail", 0.3))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        head = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist_from_center = abs(seq_i - n // 2) / max(1, n // 2)
            dist = abs(dist_from_center - head)
            if dist < tail:
                brightness = 1.0 - dist / tail
            else:
                brightness = 0.0
            ef.values[orig_i][ch] = _clamp(lo + brightness * (hi - lo))
    reg(EffectPreset(
        id="fountain_symmetric_spread", name="对称展开", category="Fountain",
        description="从中心同时向两侧扩散的光带效果",
        target_channels=["valve", "pump", "dimmer"], icon="↔️",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 2.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tail", "拖尾", "float", 0.3, 0.05, 1.0, 0.05,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_symmetric_spread)

    # A11: 螺旋上升
    def _fountain_spiral_rise(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        turns = float(params.get("turns", 2.0))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            spiral_phase = ratio * turns + t * speed
            v = (math.sin(spiral_phase * 2 * math.pi) + 1) / 2
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
    reg(EffectPreset(
        id="fountain_spiral_rise", name="螺旋上升", category="Fountain",
        description="螺旋形波纹沿设备阵列上升",
        target_channels=["valve", "pump", "dimmer"], icon="🌀",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "上升速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("turns", "螺旋圈数", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制旋转圈数。值越大旋转的总圈数越多"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_spiral_rise)

    # A12: 内抛效果
    def _fountain_inner_throw(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - n // 2) / max(1, n // 2)
            # 从外向内收缩
            if dist > (1.0 - phase):
                ef.values[orig_i][ch] = hi
            else:
                ef.values[orig_i][ch] = lo
    reg(EffectPreset(
        id="fountain_inner_throw", name="内抛效果", category="Fountain",
        description="从外侧向中心逐步收拢，模拟内抛喷泉",
        target_channels=["valve", "pump", "dimmer"], icon="⊙",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("hi", "开启值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "关闭值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_inner_throw)

    # A13: 水松效果（多层叠加高度）
    def _fountain_water_pine(ef, t, sorted_fx, fdefs, params, fps):
        layers = int(params.get("layers", 3))
        speed = float(params.get("speed", 0.5))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 60))
        ch = params.get("ch_type", "valve")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            # 多层锥形
            layer_val = 0.0
            for layer in range(layers):
                layer_center = 0.5
                layer_width = 1.0 - layer * 0.2
                layer_height = 1.0 - layer * 0.15
                dist = abs(ratio - layer_center)
                if dist < layer_width / 2:
                    contrib = (1.0 - dist / (layer_width / 2)) * layer_height
                    phase = math.sin(t * speed * 2 * math.pi + layer * 0.5)
                    contrib *= (0.7 + 0.3 * phase)
                    layer_val = max(layer_val, contrib)
            ef.values[orig_i][ch] = _clamp(lo + layer_val * (hi - lo))
    reg(EffectPreset(
        id="fountain_water_pine", name="水松效果", category="Fountain",
        description="多层锥形叠加，模拟松树形喷泉水型",
        target_channels=["valve", "pump", "dimmer"], icon="🌲",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("layers", "层数", "int", 3, 1, 6, 1,
                tooltip="控制叠加层数。值越大层数越多，效果越丰富厚重"),
            ParamSpec("speed", "脉动速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 60, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_water_pine)

    # A14: 喷泉爆发（全开后渐收）
    def _fountain_burst(ef, t, sorted_fx, fdefs, params, fps):
        period = float(params.get("period", 4.0))
        attack = float(params.get("attack", 0.1))
        sustain = float(params.get("sustain", 0.3))
        release = float(params.get("release", 0.6))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "valve")
        phase = (t % period) / period
        if phase < attack:
            env = phase / attack
        elif phase < attack + sustain:
            env = 1.0
        elif phase < attack + sustain + release:
            env = 1.0 - (phase - attack - sustain) / release
        else:
            env = 0.0
        env = _ease_in_out(env)
        val = _clamp(lo + env * (hi - lo))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="fountain_burst", name="喷泉爆发", category="Fountain",
        description="ADSR 包络：快速上升→保持→缓慢下降，模拟喷泉爆发",
        target_channels=["valve", "pump", "dimmer"], icon="💥",
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve", choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("period", "周期(秒)", "float", 4.0, 0.5, 30.0, 0.5,
                tooltip="控制效果的周期时长。值越大每个完整周期耗时越长"),
            ParamSpec("attack", "上升比例", "float", 0.1, 0.01, 0.5, 0.01,
                tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
            ParamSpec("sustain", "保持比例", "float", 0.3, 0.0, 0.8, 0.05,
                tooltip="控制效果延持电平。值越大持续阶段的输出强度越高"),
            ParamSpec("release", "下降比例", "float", 0.6, 0.1, 0.9, 0.05,
                tooltip="控制效果释音时间。值越小停止后衰减越快，值越大尾音拖曳越长"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _fountain_burst)

    # ═══════════════════════════════════════════════════════════════════════
    #  B. 高级动态效果
    # ═══════════════════════════════════════════════════════════════════════

    # B1: 渐强渐弱（Crescendo/Decrescendo）
    def _dynamic_crescendo(ef, t, sorted_fx, fdefs, params, fps):
        period = float(params.get("period", 8.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "dimmer")
        mode = params.get("mode", "渐强渐弱")
        phase = (t % period) / period
        if mode == "渐强":
            env = phase
        elif mode == "渐弱":
            env = 1.0 - phase
        else:
            env = (math.sin(phase * 2 * math.pi - math.pi / 2) + 1) / 2
        env = _ease_in_out_cubic(env)
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            delay = seq_i / max(1, n) * 0.1
            local_phase = ((t - delay) % period) / period
            if mode == "渐强":
                local_env = local_phase
            elif mode == "渐弱":
                local_env = 1.0 - local_phase
            else:
                local_env = (math.sin(local_phase * 2 * math.pi - math.pi / 2) + 1) / 2
            local_env = _ease_in_out_cubic(max(0, min(1, local_env)))
            ef.values[orig_i][ch] = _clamp(lo + local_env * (hi - lo))
    reg(EffectPreset(
        id="dynamic_crescendo", name="渐强渐弱", category="Wave",
        description="整体渐强或渐弱，带微小延迟产生层次感",
        target_channels=["dimmer", "valve", "pump"], icon="📈",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("period", "周期(秒)", "float", 8.0, 1.0, 60.0, 1.0,
                tooltip="控制效果的周期时长。值越大每个完整周期耗时越长"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("mode", "模式", "choice", "渐强渐弱", choices=["渐强", "渐弱", "渐强渐弱"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _dynamic_crescendo)

    # B2: 力度映射（Velocity Mapping）
    def _dynamic_velocity(ef, t, sorted_fx, fdefs, params, fps):
        bpm = float(params.get("bpm", 120.0))
        attack = float(params.get("attack", 0.05))
        decay = float(params.get("decay", 0.3))
        velocity = int(params.get("velocity", 255))
        base = int(params.get("base", 20))
        ch = params.get("ch_type", "dimmer")
        beat_period = 60.0 / bpm
        beat_phase = (t % beat_period) / beat_period
        if beat_phase < attack:
            env = beat_phase / attack
        elif beat_phase < attack + decay:
            env = 1.0 - (beat_phase - attack) / decay * 0.7
        else:
            env = 0.3 * (1.0 - (beat_phase - attack - decay) / (1.0 - attack - decay))
        env = max(0, min(1, env))
        val = _clamp(base + env * (velocity - base))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="dynamic_velocity", name="力度映射", category="Wave",
        description="模拟打击乐力度：快速上升→自然衰减，产生有力的节拍感",
        target_channels=["dimmer", "valve", "pump"], icon="🥁",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("attack", "起音", "float", 0.05, 0.01, 0.3, 0.01,
                tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
            ParamSpec("decay", "衰减", "float", 0.3, 0.05, 0.9, 0.05,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
            ParamSpec("velocity", "力度", "int", 255, 0, 255, 5,
                tooltip="控制运动速度矢量。值越大设备运动越快"),
            ParamSpec("base", "基准值", "int", 20, 0, 255, 5,
                tooltip="控制效果的基准值。所有变化以此值为中心上下波动"),
        ],
    ), _dynamic_velocity)

    # B3: BPM 分频器
    def _dynamic_bpm_divider(ef, t, sorted_fx, fdefs, params, fps):
        bpm = float(params.get("bpm", 120.0))
        divider = int(params.get("divider", 2))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        beat_period = 60.0 / bpm * divider
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase_offset = seq_i / max(1, n) * beat_period / divider
            local_phase = ((t + phase_offset) % beat_period) / beat_period
            v = (math.sin(local_phase * 2 * math.pi) + 1) / 2
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
    reg(EffectPreset(
        id="dynamic_bpm_divider", name="BPM 分频器", category="Wave",
        description="基于 BPM 的分频波形，支持 1/2, 1/4, 1/8 等节拍分割",
        target_channels=["dimmer", "valve", "pump"], icon="🎵",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("divider", "分频(1=全拍)", "int", 2, 1, 16, 1,
                tooltip="控制分频比。1=每拍触发，2=每两拍触发，依次类推"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _dynamic_bpm_divider)

    # B4: MA Phaser 多步循环
    def _ma_phaser(ef, t, sorted_fx, fdefs, params, fps):
        steps = int(params.get("steps", 4))
        speed = float(params.get("speed", 1.0))
        spread = float(params.get("spread", 0.5))
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        step_values = []
        for s in range(steps):
            step_values.append(int(255 * s / max(1, steps - 1)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = (t * speed + seq_i * spread / max(1, n)) % 1.0
            step_f = phase * steps
            step_idx = int(step_f) % steps
            next_idx = (step_idx + 1) % steps
            frac = step_f - int(step_f)
            frac = _ease_in_out(frac)
            val = step_values[step_idx] + frac * (step_values[next_idx] - step_values[step_idx])
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="ma_phaser", name="MA Phaser", category="Wave",
        description="MA 控台风格多步循环效果，步骤间平滑过渡",
        target_channels=["dimmer", "valve", "pump", "motor_x", "motor_y"], icon="🔮",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "valve", "pump", "motor_x", "motor_y"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("steps", "步骤数", "int", 4, 2, 16, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("spread", "扩散", "float", 0.5, 0.0, 2.0, 0.1,
                tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ],
    ), _ma_phaser)

    # B5: MA Fan 分布
    def _ma_fan(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "dimmer")
        curve = params.get("curve", "线性")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            if curve == "S曲线":
                ratio = _ease_in_out(ratio)
            elif curve == "指数":
                ratio = ratio ** 2
            elif curve == "对数":
                ratio = math.sqrt(ratio)
            ef.values[orig_i][ch] = _clamp(lo + ratio * (hi - lo))
    reg(EffectPreset(
        id="ma_fan", name="MA Fan 分布", category="Wave",
        description="MA 控台风格 Fan 功能：将值均匀分布到所有设备",
        target_channels=["dimmer", "valve", "pump", "motor_x", "motor_y"], icon="📊",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "valve", "pump", "motor_x", "motor_y"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("lo", "起始值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "结束值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("curve", "曲线", "choice", "线性", choices=["线性", "S曲线", "指数", "对数"],
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _ma_fan)

    # B6: Delay 延迟链
    def _delay_chain(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        delay_per = float(params.get("delay", 0.1))
        wave = params.get("wave", "Sine")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "dimmer")
        wave_fn = WAVE_FUNCS.get(wave, WAVE_FUNCS["Sine"])
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            local_t = t - seq_i * delay_per
            if local_t < 0:
                v = 0.0
            else:
                v = wave_fn(local_t * speed)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
    reg(EffectPreset(
        id="delay_chain", name="延迟链", category="Wave",
        description="每个设备延迟固定时间触发，形成级联效果",
        target_channels=["dimmer", "valve", "pump"], icon="⏱️",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "波形速度", "float", 2.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("delay", "每设备延迟(秒)", "float", 0.1, 0.01, 2.0, 0.01,
                tooltip="控制效果的延迟/错开时间。值越大效果启动越滞后"),
            ParamSpec("wave", "波形", "choice", "Sine",
                      choices=["Sine", "Square", "Triangle", "Sawtooth", "Bounce"],
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _delay_chain)

    # B7: 随机火花
    def _random_sparkle(ef, t, sorted_fx, fdefs, params, fps):
        density = float(params.get("density", 0.2))
        fade = float(params.get("fade", 0.3))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        frame_idx = int(t * fps)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = hash((frame_idx // max(1, int(fade * fps)), seq_i)) % 1000
            if seed < density * 1000:
                sub_phase = (frame_idx % max(1, int(fade * fps))) / max(1, fade * fps)
                brightness = 1.0 - sub_phase
            else:
                brightness = 0.0
            ef.values[orig_i][ch] = _clamp(lo + brightness * (hi - lo))
    reg(EffectPreset(
        id="random_sparkle", name="随机火花", category="Wave",
        description="随机位置闪烁并衰减，如星空闪烁",
        target_channels=["dimmer", "valve", "pump"], icon="✨",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("density", "密度", "float", 0.2, 0.01, 1.0, 0.01,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("fade", "衰减时间(秒)", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果渐弱/消退时长。值越大消退越平缓自然"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _random_sparkle)

    # B8: 心跳效果
    def _heartbeat(ef, t, sorted_fx, fdefs, params, fps):
        bpm = float(params.get("bpm", 72.0))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        beat_period = 60.0 / bpm
        phase = (t % beat_period) / beat_period
        # 双峰心跳波形
        if phase < 0.1:
            env = phase / 0.1
        elif phase < 0.15:
            env = 1.0 - (phase - 0.1) / 0.05 * 0.6
        elif phase < 0.25:
            env = 0.4 + (phase - 0.15) / 0.1 * 0.6
        elif phase < 0.35:
            env = 1.0 - (phase - 0.25) / 0.1
        else:
            env = 0.0
        val = _clamp(lo + env * (hi - lo))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="heartbeat", name="心跳效果", category="Wave",
        description="模拟心跳的双峰波形，适合情感表达",
        target_channels=["dimmer", "valve", "pump"], icon="💓",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm", "心率(BPM)", "float", 72.0, 30.0, 200.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _heartbeat)

    # ═══════════════════════════════════════════════════════════════════════
    #  C. 颜色效果预设
    # ═══════════════════════════════════════════════════════════════════════

    # C1: 渐变彩虹（流彩）
    def _color_gradient_rainbow(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        repeats = int(params.get("repeats", 1))
        saturation = float(params.get("saturation", 1.0))
        brightness = float(params.get("brightness", 1.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            hue = (seq_i / max(1, n) * repeats + t * speed) % 1.0
            # HSV to RGB
            h = hue * 6.0
            c = saturation * brightness
            x = c * (1 - abs(h % 2 - 1))
            m = brightness - c
            if h < 1: r, g, b = c, x, 0
            elif h < 2: r, g, b = x, c, 0
            elif h < 3: r, g, b = 0, c, x
            elif h < 4: r, g, b = 0, x, c
            elif h < 5: r, g, b = x, 0, c
            else: r, g, b = c, 0, x
            ef.values[orig_i]["R"] = _clamp((r + m) * 255)
            ef.values[orig_i]["G"] = _clamp((g + m) * 255)
            ef.values[orig_i]["B"] = _clamp((b + m) * 255)
    reg(EffectPreset(
        id="color_gradient_rainbow", name="渐变彩虹", category="Color",
        description="流彩彩虹渐变，沿设备阵列流动",
        target_channels=["R", "G", "B"], icon="🌈",
        params=[
            ParamSpec("speed", "流动速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("repeats", "重复次数", "int", 1, 1, 8, 1,
                tooltip="控制重复次数。值越大效果重复播放的次数越多"),
            ParamSpec("saturation", "饱和度", "float", 1.0, 0.0, 1.0, 0.05,
                tooltip="控制色彩饱和度。0 为灰度无彩色，值越高色彩越鲜艳"),
            ParamSpec("brightness", "亮度", "float", 1.0, 0.0, 1.0, 0.05,
                tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
        ],
    ), _color_gradient_rainbow)

    # C2: 火焰效果
    def _color_fire(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        intensity = float(params.get("intensity", 0.8))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            noise = (math.sin(t * speed + seq_i * 1.7) * 0.5 +
                     math.sin(t * speed * 1.3 + seq_i * 2.3) * 0.3 +
                     math.sin(t * speed * 0.7 + seq_i * 3.1) * 0.2)
            noise = (noise + 1) / 2 * intensity
            # 火焰色：红→橙→黄
            ef.values[orig_i]["R"] = _clamp(255 * noise)
            ef.values[orig_i]["G"] = _clamp(120 * noise * noise)
            ef.values[orig_i]["B"] = _clamp(30 * noise * noise * noise)
    reg(EffectPreset(
        id="color_fire", name="火焰效果", category="Color",
        description="模拟火焰跳动的暖色效果",
        target_channels=["R", "G", "B"], icon="🔥",
        params=[
            ParamSpec("speed", "跳动速度", "float", 3.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("intensity", "强度", "float", 0.8, 0.1, 1.0, 0.05,
                tooltip="控制效果的整体强度。值为 0 时无效果，最大值对应最强烈的输出"),
        ],
    ), _color_fire)

    # C3: 海洋波浪色
    def _color_ocean(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        depth = float(params.get("depth", 0.5))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            wave = (math.sin(t * speed * 2 * math.pi + ratio * 4 * math.pi) + 1) / 2
            ef.values[orig_i]["R"] = _clamp(20 * (1 - depth))
            ef.values[orig_i]["G"] = _clamp(80 + 80 * wave * depth)
            ef.values[orig_i]["B"] = _clamp(150 + 105 * wave)
    reg(EffectPreset(
        id="color_ocean", name="海洋波浪", category="Color",
        description="蓝绿色调的海洋波浪效果",
        target_channels=["R", "G", "B"], icon="🌊",
        params=[
            ParamSpec("speed", "波浪速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("depth", "深度", "float", 0.5, 0.0, 1.0, 0.05,
                tooltip="控制深度的比例。0 为最小，1.0 为最大"),
        ],
    ), _color_ocean)

    # C4: 极光效果
    def _color_aurora(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            s1 = math.sin(t * speed + ratio * 3) * 0.5 + 0.5
            s2 = math.sin(t * speed * 0.7 + ratio * 5 + 1.0) * 0.5 + 0.5
            s3 = math.sin(t * speed * 1.3 + ratio * 2 + 2.0) * 0.5 + 0.5
            ef.values[orig_i]["R"] = _clamp(50 * s1 + 30 * s3)
            ef.values[orig_i]["G"] = _clamp(200 * s2 + 55 * s1)
            ef.values[orig_i]["B"] = _clamp(100 * s3 + 155 * s2)
    reg(EffectPreset(
        id="color_aurora", name="极光效果", category="Color",
        description="多层正弦叠加产生极光般的柔和色彩变化",
        target_channels=["R", "G", "B"], icon="🌌",
        params=[
            ParamSpec("speed", "变化速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _color_aurora)

    # C5: 双色交替
    def _color_two_tone(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        r1, g1, b1 = int(params.get("r1", 255)), int(params.get("g1", 0)), int(params.get("b1", 0))
        r2, g2, b2 = int(params.get("r2", 0)), int(params.get("g2", 0)), int(params.get("b2", 255))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            mix = (math.sin(t * speed * 2 * math.pi + seq_i * math.pi / max(1, n)) + 1) / 2
            ef.values[orig_i]["R"] = _clamp(r1 + mix * (r2 - r1))
            ef.values[orig_i]["G"] = _clamp(g1 + mix * (g2 - g1))
            ef.values[orig_i]["B"] = _clamp(b1 + mix * (b2 - b1))
    reg(EffectPreset(
        id="color_two_tone", name="双色交替", category="Color",
        description="两种颜色之间平滑交替变化",
        target_channels=["R", "G", "B"], icon="🎨",
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("r1", "颜色1-R", "int", 255, 0, 255, 5,
                tooltip="控制第一种颜色的红色通道强度。0=无红色，255=满红"),
            ParamSpec("g1", "颜色1-G", "int", 0, 0, 255, 5,
                tooltip="控制第一种颜色的绿色通道强度。0=无绿色，255=满绿"),
            ParamSpec("b1", "颜色1-B", "int", 0, 0, 255, 5,
                tooltip="控制第一种颜色的蓝色通道强度。0=无蓝色，255=满蓝"),
            ParamSpec("r2", "颜色2-R", "int", 0, 0, 255, 5,
                tooltip="控制第二种颜色的红色通道强度。0=无红色，255=满红"),
            ParamSpec("g2", "颜色2-G", "int", 0, 0, 255, 5,
                tooltip="控制第二种颜色的绿色通道强度。0=无绿色，255=满绿"),
            ParamSpec("b2", "颜色2-B", "int", 255, 0, 255, 5,
                tooltip="控制第二种颜色的蓝色通道强度。0=无蓝色，255=满蓝"),
        ],
    ), _color_two_tone)

    # C6: 色温日出日落
    def _color_sunrise(ef, t, sorted_fx, fdefs, params, fps):
        period = float(params.get("period", 10.0))
        phase = (t % period) / period
        # 日出色温变化：暗红→橙→白→蓝白→白→橙→暗红
        if phase < 0.5:
            p = phase * 2
        else:
            p = (1.0 - phase) * 2
        p = _ease_in_out(p)
        r = _clamp(80 + 175 * p)
        g = _clamp(20 + 235 * p * p)
        b = _clamp(10 + 245 * p * p * p)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b
    reg(EffectPreset(
        id="color_sunrise", name="日出日落", category="Color",
        description="模拟日出到日落的色温变化过程",
        target_channels=["R", "G", "B"], icon="🌅",
        params=[
            ParamSpec("period", "周期(秒)", "float", 10.0, 2.0, 60.0, 1.0,
                tooltip="控制效果的周期时长。值越大每个完整周期耗时越长"),
        ],
    ), _color_sunrise)

    # ═══════════════════════════════════════════════════════════════════════
    #  D. 位置轨迹效果（Position-FX）
    # ═══════════════════════════════════════════════════════════════════════

    # D1: 利萨如图形
    def _pos_lissajous(ef, t, sorted_fx, fdefs, params, fps):
        freq_x = float(params.get("freq_x", 3.0))
        freq_y = float(params.get("freq_y", 2.0))
        phase_diff = float(params.get("phase_diff", 0.5))
        size = float(params.get("size", 1.0))
        speed = float(params.get("speed", 1.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / max(1, n) * phase_diff
            x = (math.sin(2 * math.pi * freq_x * (t * speed + offset)) + 1) / 2
            y = (math.sin(2 * math.pi * freq_y * (t * speed + offset) + math.pi * phase_diff) + 1) / 2
            ef.values[orig_i]["motor_x"] = _clamp(x * 255 * size)
            ef.values[orig_i]["motor_y"] = _clamp(y * 255 * size)
    reg(EffectPreset(
        id="pos_lissajous", name="利萨如图形", category="Position",
        description="XY 轴按不同频率运动形成利萨如曲线",
        target_channels=["motor_x", "motor_y"], icon="∞",
        params=[
            ParamSpec("freq_x", "X频率", "float", 3.0, 1.0, 10.0, 0.5,
                tooltip="控制 X 轴方向的波动频率。值越高水平方向波动越密集"),
            ParamSpec("freq_y", "Y频率", "float", 2.0, 1.0, 10.0, 0.5,
                tooltip="控制 Y 轴方向的波动频率。值越高垂直方向波动越密集"),
            ParamSpec("phase_diff", "相位差", "float", 0.5, 0.0, 2.0, 0.1,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("size", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果元素的尺寸大小。值越大单个元素的视觉面积越大"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _pos_lissajous)

    # D2: 心形轨迹
    def _pos_heart(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        size = float(params.get("size", 0.8))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / max(1, n) * 0.3
            angle = (t * speed + offset) * 2 * math.pi
            x = 16 * math.sin(angle) ** 3
            y = 13 * math.cos(angle) - 5 * math.cos(2*angle) - 2 * math.cos(3*angle) - math.cos(4*angle)
            ef.values[orig_i]["motor_x"] = _clamp((x / 32 * size + 0.5) * 255)
            ef.values[orig_i]["motor_y"] = _clamp((y / 32 * size + 0.5) * 255)
    reg(EffectPreset(
        id="pos_heart", name="心形轨迹", category="Position",
        description="XY 运动形成心形图案",
        target_channels=["motor_x", "motor_y"], icon="❤️",
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("size", "大小", "float", 0.8, 0.1, 1.0, 0.05,
                tooltip="控制效果元素的尺寸大小。值越大单个元素的视觉面积越大"),
        ],
    ), _pos_heart)

    # D3: 星形轨迹
    def _pos_star(ef, t, sorted_fx, fdefs, params, fps):
        points = int(params.get("points", 5))
        speed = float(params.get("speed", 0.3))
        size = float(params.get("size", 0.8))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / max(1, n) * 0.2
            angle = (t * speed + offset) * 2 * math.pi
            # 星形极坐标
            r = 0.5 + 0.3 * math.cos(points * angle)
            x = r * math.cos(angle) * size
            y = r * math.sin(angle) * size
            ef.values[orig_i]["motor_x"] = _clamp((x + 0.5) * 255)
            ef.values[orig_i]["motor_y"] = _clamp((y + 0.5) * 255)
    reg(EffectPreset(
        id="pos_star", name="星形轨迹", category="Position",
        description="XY 运动形成星形图案",
        target_channels=["motor_x", "motor_y"], icon="⭐",
        params=[
            ParamSpec("points", "角数", "int", 5, 3, 12, 1,
                tooltip="控制参考点或顶点的数量。值越大形状轮廓越精细"),
            ParamSpec("speed", "速度", "float", 0.3, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("size", "大小", "float", 0.8, 0.1, 1.0, 0.05,
                tooltip="控制效果元素的尺寸大小。值越大单个元素的视觉面积越大"),
        ],
    ), _pos_star)

    # D4: 螺旋轨迹
    def _pos_spiral(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        expansion = float(params.get("expansion", 2.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / max(1, n) * 0.5
            angle = (t * speed + offset) * 2 * math.pi * expansion
            r = ((t * speed + offset) % 1.0) * 0.4
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            ef.values[orig_i]["motor_x"] = _clamp((x + 0.5) * 255)
            ef.values[orig_i]["motor_y"] = _clamp((y + 0.5) * 255)
    reg(EffectPreset(
        id="pos_spiral", name="螺旋轨迹", category="Position",
        description="由内向外螺旋展开的运动轨迹",
        target_channels=["motor_x", "motor_y"], icon="🌀",
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("expansion", "展开圈数", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制扩张的圈数。值越大向外扩散的层次越多"),
        ],
    ), _pos_spiral)

    # D5: 随机游走
    def _pos_random_walk(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        smoothness = float(params.get("smoothness", 0.5))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 用多个正弦叠加模拟随机运动
            x = (math.sin(t * speed * 1.0 + seq_i * 7.3) * 0.3 +
                 math.sin(t * speed * 2.3 + seq_i * 3.1) * 0.2 +
                 math.sin(t * speed * 0.7 + seq_i * 11.7) * 0.15 + 0.5)
            y = (math.sin(t * speed * 1.3 + seq_i * 5.7) * 0.3 +
                 math.sin(t * speed * 1.7 + seq_i * 9.3) * 0.2 +
                 math.sin(t * speed * 0.5 + seq_i * 13.1) * 0.15 + 0.5)
            ef.values[orig_i]["motor_x"] = _clamp(x * 255)
            ef.values[orig_i]["motor_y"] = _clamp(y * 255)
    reg(EffectPreset(
        id="pos_random_walk", name="随机游走", category="Position",
        description="伪随机的柔和运动轨迹",
        target_channels=["motor_x", "motor_y"], icon="🎲",
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("smoothness", "平滑度", "float", 0.5, 0.0, 1.0, 0.05,
                tooltip="控制平滑程度。值越大输出曲线越光滑，但细节损失越多"),
        ],
    ), _pos_random_walk)

    # D6: 线性扫描
    def _pos_linear_scan(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        axis = params.get("axis", "X轴")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        phase = (t * speed) % 1.0
        # 来回扫描
        val = int(lo + (math.sin(phase * 2 * math.pi) + 1) / 2 * (hi - lo))
        ch = "motor_x" if axis == "X轴" else "motor_y"
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / max(1, n) * 0.2
            local_val = int(lo + (math.sin((phase + offset) * 2 * math.pi) + 1) / 2 * (hi - lo))
            ef.values[orig_i][ch] = _clamp(local_val)
    reg(EffectPreset(
        id="pos_linear_scan", name="线性扫描", category="Position",
        description="单轴来回扫描运动",
        target_channels=["motor_x", "motor_y"], icon="↔️",
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("axis", "轴", "choice", "X轴", choices=["X轴", "Y轴"],
                tooltip="选择参考轴方向。决定效果沿哪个空间维度展开"),
            ParamSpec("lo", "最小位置", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "最大位置", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pos_linear_scan)

    # ═══════════════════════════════════════════════════════════════════════
    #  E. 矩阵图案高级预设
    # ═══════════════════════════════════════════════════════════════════════

    # E1: 棋盘翻转
#     def _matrix_checkerboard(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         hi = int(params.get("hi", 255))
#         lo = int(params.get("lo", 0))
#         ch = params.get("ch_type", "dimmer")
#         n = len(sorted_fx)
#         cols = int(math.ceil(math.sqrt(n)))
#         flip = int(t * speed) % 2
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             if (row + col + flip) % 2 == 0:
#                 ef.values[orig_i][ch] = hi
#             else:
#                 ef.values[orig_i][ch] = lo
#     reg(EffectPreset(
#         id="matrix_checkerboard", name="棋盘翻转", category="Matrix",
#         description="棋盘格图案定时翻转",
#         target_channels=["dimmer", "valve", "R", "G", "B"], icon="♟️",
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "R", "G", "B"]),
#             ParamSpec("speed", "翻转速度", "float", 1.0, 0.1, 10.0, 0.1),
#             ParamSpec("hi", "亮值", "int", 255, 0, 255, 5),
#             ParamSpec("lo", "暗值", "int", 0, 0, 255, 5),
#         ],
#     ), _matrix_checkerboard)

    # E2: 生命游戏
#     def _matrix_life(ef, t, sorted_fx, fdefs, params, fps):
#         hi = int(params.get("hi", 255))
#         lo = int(params.get("lo", 0))
#         ch = params.get("ch_type", "dimmer")
#         speed = float(params.get("speed", 2.0))
#         n = len(sorted_fx)
#         cols = int(math.ceil(math.sqrt(n)))
#         rows = int(math.ceil(n / cols))
#         gen = int(t * speed)
        # 用确定性种子生成状态
#         random.seed(gen * 42)
#         grid = [[random.random() > 0.6 for _ in range(cols)] for _ in range(rows)]
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             if row < rows and col < cols and grid[row][col]:
#                 ef.values[orig_i][ch] = hi
#             else:
#                 ef.values[orig_i][ch] = lo
#     reg(EffectPreset(
#         id="matrix_life", name="生命游戏", category="Matrix",
#         description="类似康威生命游戏的随机矩阵图案",
#         target_channels=["dimmer", "valve", "R", "G", "B"], icon="🧬",
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "R", "G", "B"]),
#             ParamSpec("speed", "演化速度", "float", 2.0, 0.5, 10.0, 0.5),
#             ParamSpec("hi", "活值", "int", 255, 0, 255, 5),
#             ParamSpec("lo", "死值", "int", 0, 0, 255, 5),
#         ],
#     ), _matrix_life)

    # E3: 粒子系统
#     def _matrix_particles(ef, t, sorted_fx, fdefs, params, fps):
#         count = int(params.get("count", 5))
#         speed = float(params.get("speed", 2.0))
#         fade = float(params.get("fade", 0.5))
#         hi = int(params.get("hi", 255))
#         ch = params.get("ch_type", "dimmer")
#         n = len(sorted_fx)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             brightness = 0.0
#             for p in range(count):
                # 每个粒子有不同的速度和起始位置
#                 p_pos = ((t * speed * (0.5 + p * 0.3) + p * 0.17) % 1.0) * n
#                 dist = abs(seq_i - p_pos)
#                 if dist < fade * n:
#                     brightness = max(brightness, 1.0 - dist / (fade * n))
#             ef.values[orig_i][ch] = _clamp(brightness * hi)
#     reg(EffectPreset(
#         id="matrix_particles", name="粒子系统", category="Matrix",
#         description="多个粒子在设备阵列上移动并衰减",
#         target_channels=["dimmer", "valve"], icon="⚛️",
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve"]),
#             ParamSpec("count", "粒子数", "int", 5, 1, 20, 1),
#             ParamSpec("speed", "速度", "float", 2.0, 0.1, 10.0, 0.1),
#             ParamSpec("fade", "衰减范围", "float", 0.5, 0.05, 1.0, 0.05),
#             ParamSpec("hi", "峰值", "int", 255, 0, 255, 5),
#         ],
#     ), _matrix_particles)

    # E4: 文字滚动（简化版：数字 0-9 的点阵）
#     def _matrix_text_scroll(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         hi = int(params.get("hi", 255))
#         lo = int(params.get("lo", 0))
#         ch = params.get("ch_type", "dimmer")
#         n = len(sorted_fx)
        # 简化：用正弦波模拟滚动文字的明暗变化
#         offset = t * speed
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             pos = (seq_i / max(1, n) + offset) % 1.0
            # 模拟文字笔画的密度变化
#             density = (math.sin(pos * 20 * math.pi) * 0.3 +
#                        math.sin(pos * 7 * math.pi) * 0.4 +
#                        math.sin(pos * 3 * math.pi) * 0.3)
#             density = max(0, (density + 1) / 2)
#             ef.values[orig_i][ch] = _clamp(lo + density * (hi - lo))
#     reg(EffectPreset(
#         id="matrix_text_scroll", name="文字滚动", category="Matrix",
#         description="模拟文字滚动的明暗图案",
#         target_channels=["dimmer", "valve"], icon="📜",
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve"]),
#             ParamSpec("speed", "滚动速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("hi", "亮值", "int", 255, 0, 255, 5),
#             ParamSpec("lo", "暗值", "int", 0, 0, 255, 5),
#         ],
#     ), _matrix_text_scroll)

    # E5: 对角线扫描
#     def _matrix_diagonal(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         width = float(params.get("width", 0.2))
#         hi = int(params.get("hi", 255))
#         lo = int(params.get("lo", 0))
#         ch = params.get("ch_type", "dimmer")
#         n = len(sorted_fx)
#         cols = int(math.ceil(math.sqrt(n)))
#         head = (t * speed) % 2.0  # 0-2 范围（对角线长度）
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             diag = (row + col) / max(1, cols + cols - 2)  # 归一化对角线位置
#             dist = abs(diag - head / 2)
#             if dist < width:
#                 brightness = 1.0 - dist / width
#             else:
#                 brightness = 0.0
#             ef.values[orig_i][ch] = _clamp(lo + brightness * (hi - lo))
#     reg(EffectPreset(
#         id="matrix_diagonal", name="对角线扫描", category="Matrix",
#         description="光带沿对角线方向扫描矩阵",
#         target_channels=["dimmer", "valve", "R", "G", "B"], icon="↗️",
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "R", "G", "B"]),
#             ParamSpec("speed", "速度", "float", 2.0, 0.1, 10.0, 0.1),
#             ParamSpec("width", "光带宽度", "float", 0.2, 0.05, 0.5, 0.05),
#             ParamSpec("hi", "峰值", "int", 255, 0, 255, 5),
#             ParamSpec("lo", "底值", "int", 0, 0, 255, 5),
#         ],
#     ), _matrix_diagonal)

    # E6: 同心圆扩散
#     def _matrix_concentric(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.5))
#         rings = int(params.get("rings", 3))
#         hi = int(params.get("hi", 255))
#         lo = int(params.get("lo", 0))
#         ch = params.get("ch_type", "dimmer")
#         n = len(sorted_fx)
#         cols = int(math.ceil(math.sqrt(n)))
#         cx, cy = cols / 2, cols / 2
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             dist = math.sqrt((row - cy) ** 2 + (col - cx) ** 2) / max(1, cols)
#             wave = (math.sin((dist * rings - t * speed) * 2 * math.pi) + 1) / 2
#             ef.values[orig_i][ch] = _clamp(lo + wave * (hi - lo))
#     reg(EffectPreset(
#         id="matrix_concentric", name="同心圆扩散", category="Matrix",
#         description="从中心向外扩散的同心圆波纹",
#         target_channels=["dimmer", "valve", "R", "G", "B"], icon="◎",
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "R", "G", "B"]),
#             ParamSpec("speed", "扩散速度", "float", 1.5, 0.1, 5.0, 0.1),
#             ParamSpec("rings", "波纹数", "int", 3, 1, 10, 1),
#             ParamSpec("hi", "峰值", "int", 255, 0, 255, 5),
#             ParamSpec("lo", "底值", "int", 0, 0, 255, 5),
#         ],
#     ), _matrix_concentric)

    # ═══════════════════════════════════════════════════════════════════════
    #  F. 组合/高级效果
    # ═══════════════════════════════════════════════════════════════════════

    # F1: 呼吸+追逐复合
    def _combo_breathe_chase(ef, t, sorted_fx, fdefs, params, fps):
        breathe_speed = float(params.get("breathe_speed", 0.5))
        chase_speed = float(params.get("chase_speed", 3.0))
        mix = float(params.get("mix", 0.5))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        breathe = (math.sin(t * breathe_speed * 2 * math.pi) + 1) / 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            chase_phase = (t * chase_speed + seq_i * 0.2) % 1.0
            chase = max(0, 1.0 - chase_phase * 3) if chase_phase < 0.33 else 0
            combined = breathe * (1 - mix) + chase * mix
            ef.values[orig_i][ch] = _clamp(combined * hi)
    reg(EffectPreset(
        id="combo_breathe_chase", name="呼吸追逐", category="Wave",
        description="呼吸效果与追逐效果的混合",
        target_channels=["dimmer", "valve", "pump"], icon="🌬️",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("breathe_speed", "呼吸速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制呼吸效果的速度——一呼一吸的完整周期时长。值越小呼吸越舒缓"),
            ParamSpec("chase_speed", "追逐速度", "float", 3.0, 0.5, 10.0, 0.5,
                tooltip="控制跑灯/追逐效果的移动速度。值越大光点跑动越快"),
            ParamSpec("mix", "混合比例", "float", 0.5, 0.0, 1.0, 0.05,
                tooltip="控制两种颜色/效果之间的混合比例。0=纯第一种，1=纯第二种"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _combo_breathe_chase)

    # F2: 音乐重音触发
    def _music_accent(ef, t, sorted_fx, fdefs, params, fps):
        bpm = float(params.get("bpm", 120.0))
        accent_every = int(params.get("accent_every", 4))
        normal_val = int(params.get("normal_val", 100))
        accent_val = int(params.get("accent_val", 255))
        decay = float(params.get("decay", 0.3))
        ch = params.get("ch_type", "dimmer")
        beat_period = 60.0 / bpm
        beat_num = int(t / beat_period)
        beat_phase = (t % beat_period) / beat_period
        is_accent = (beat_num % accent_every) == 0
        if is_accent:
            if beat_phase < decay:
                env = 1.0 - beat_phase / decay * 0.5
            else:
                env = 0.5
            val = _clamp(accent_val * env)
        else:
            if beat_phase < 0.1:
                env = 1.0
            elif beat_phase < 0.1 + decay:
                env = 1.0 - (beat_phase - 0.1) / decay * 0.7
            else:
                env = 0.3
            val = _clamp(normal_val * env)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="music_accent", name="重音触发", category="Audio",
        description="每 N 拍强调一次，模拟音乐重音",
        target_channels=["dimmer", "valve", "pump"], icon="🎵",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm", "BPM", "float", 120.0, 30.0, 300.0, 1.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("accent_every", "每N拍重音", "int", 4, 2, 16, 1,
                tooltip="设置每隔 N 拍插入一个重音效果。值越大重音越稀疏"),
            ParamSpec("normal_val", "普通力度", "int", 100, 0, 255, 5,
                tooltip="控制正常/常规状态下的输出值。作为效果未触发时的默认状态"),
            ParamSpec("accent_val", "重音力度", "int", 255, 0, 255, 5,
                tooltip="控制重音触发时的输出峰值。值越高重音越突出"),
            ParamSpec("decay", "衰减", "float", 0.3, 0.05, 0.9, 0.05,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
        ],
    ), _music_accent)

    # F3: 频闪（可调占空比）
    def _strobe_duty(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 10.0))
        duty = float(params.get("duty", 0.1))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        phase = (t * freq) % 1.0
        val = hi if phase < duty else lo
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="strobe_duty", name="频闪(可调)", category="Wave",
        description="可调频率和占空比的频闪效果",
        target_channels=["dimmer", "valve", "pump"], icon="⚡",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq", "频率(Hz)", "float", 10.0, 0.5, 50.0, 0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("duty", "占空比", "float", 0.1, 0.01, 0.9, 0.01,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
            ParamSpec("hi", "亮值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "暗值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _strobe_duty)

    # F4: 渐变频闪（频率随时间变化）
    def _strobe_ramp(ef, t, sorted_fx, fdefs, params, fps):
        freq_lo = float(params.get("freq_lo", 2.0))
        freq_hi = float(params.get("freq_hi", 20.0))
        period = float(params.get("period", 4.0))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        ramp = (t % period) / period
        freq = freq_lo + ramp * (freq_hi - freq_lo)
        phase = (t * freq) % 1.0
        val = hi if phase < 0.5 else lo
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="strobe_ramp", name="渐变频闪", category="Wave",
        description="频闪频率从低到高渐变，产生加速感",
        target_channels=["dimmer", "valve", "pump"], icon="⚡",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq_lo", "起始频率", "float", 2.0, 0.5, 20.0, 0.5,
                tooltip="控制频率变化的下限边界。值越低波动越舒缓"),
            ParamSpec("freq_hi", "结束频率", "float", 20.0, 5.0, 50.0, 1.0,
                tooltip="控制频率变化范围的上限。值越高波动越急促"),
            ParamSpec("period", "渐变周期(秒)", "float", 4.0, 1.0, 30.0, 1.0,
                tooltip="控制效果的周期时长。值越大每个完整周期耗时越长"),
            ParamSpec("hi", "亮值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "暗值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _strobe_ramp)

    # F5: 阶梯上升
    def _staircase(ef, t, sorted_fx, fdefs, params, fps):
        steps = int(params.get("steps", 8))
        speed = float(params.get("speed", 1.0))
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        current_step = int(t * speed) % steps
        val = _clamp(lo + current_step / max(1, steps - 1) * (hi - lo))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="staircase", name="阶梯上升", category="Wave",
        description="值按阶梯逐步上升，到顶后重置",
        target_channels=["dimmer", "valve", "pump"], icon="📶",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("steps", "阶梯数", "int", 8, 2, 32, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("hi", "最高值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "最低值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _staircase)

    # F6: 镜像波浪
    def _mirror_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wave = params.get("wave", "Sine")
        hi = int(params.get("hi", 255))
        lo = int(params.get("lo", 0))
        ch = params.get("ch_type", "dimmer")
        wave_fn = WAVE_FUNCS.get(wave, WAVE_FUNCS["Sine"])
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 镜像：两端同步，中间相位最大
            mirror_pos = abs(2.0 * seq_i / max(1, n - 1) - 1.0)
            v = wave_fn(t * speed + mirror_pos * 0.5)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
    reg(EffectPreset(
        id="mirror_wave", name="镜像波浪", category="Wave",
        description="从中心向两侧对称的波浪效果",
        target_channels=["dimmer", "valve", "pump"], icon="🪞",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wave", "波形", "choice", "Sine",
                      choices=["Sine", "Triangle", "Sawtooth", "Square"],
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ],
    ), _mirror_wave)

    # F7: 弹跳球
    def _bounce_ball(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        gravity = float(params.get("gravity", 0.8))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        # 模拟弹跳：每次弹起高度递减
        period = 1.0 / speed
        cycle_t = t % (period * 4)
        bounce_num = int(cycle_t / period)
        bounce_phase = (cycle_t % period) / period
        height = (1.0 - bounce_phase) * bounce_phase * 4 * (gravity ** bounce_num)
        pos = height * n
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - pos) / max(1, n)
            if dist < 0.1:
                brightness = 1.0 - dist / 0.1
            else:
                brightness = 0.0
            ef.values[orig_i][ch] = _clamp(brightness * hi)
    reg(EffectPreset(
        id="bounce_ball", name="弹跳球", category="Wave",
        description="模拟弹跳球的运动轨迹",
        target_channels=["dimmer", "valve"], icon="⚽",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 2.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("gravity", "重力衰减", "float", 0.8, 0.3, 1.0, 0.05,
                tooltip="控制重力/回落衰减强度。值越大下落或消退的速度越快"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _bounce_ball)

    # F8: 呼吸灯（带缓动曲线）
    def _smooth_breathe(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        ch = params.get("ch_type", "dimmer")
        easing = params.get("easing", "S曲线")
        raw = (math.sin(t * speed * 2 * math.pi - math.pi / 2) + 1) / 2
        if easing == "S曲线":
            raw = _ease_in_out(raw)
        elif easing == "缓入缓出":
            raw = _ease_in_out_cubic(raw)
        elif easing == "指数":
            raw = raw ** 2.2
        val = _clamp(lo + raw * (hi - lo))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="smooth_breathe", name="柔和呼吸", category="Wave",
        description="带缓动曲线的呼吸灯效果，更自然柔和",
        target_channels=["dimmer", "valve", "pump"], icon="🫁",
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=["dimmer", "valve", "pump"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "呼吸频率", "float", 0.5, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "底值", "int", 0, 0, 255, 5,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "峰值", "int", 255, 0, 255, 5,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("easing", "缓动", "choice", "S曲线", choices=["线性", "S曲线", "缓入缓出", "指数"],
                tooltip="选择缓动函数类型。控制变化速度的加速/减速节奏"),
        ],
    ), _smooth_breathe)
"""
effect_presets_v22.py — 注册入口
"""
