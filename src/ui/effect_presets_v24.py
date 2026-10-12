"""
effect_presets_v24.py — 喷泉专用效果库大幅扩充 (v24)
====================================================
根据音乐喷泉实际动作库参考表格，大幅扩充各类型通道的效果预设：
  - 灯光(Light)：跑灯、彩色跑、渐变跑、跳跑、间N跑、七彩变化、闪烁
  - 泵/变频器(Pump)：矩形跑、三角跑、锯齿跑、山型跑、波浪跑、旗帜跑、慢升快降、放射跑
  - 阀门(Valve)：正开/反开/边开/中开、渐开渐关、跑一次
  - 数控电机(Motor)：龙摆、旋转、圆摇、交叉摆、开合屏、扇形摆、凤尾摆、海鸥摆
"""
from __future__ import annotations
import math
import random
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec


def _register_v24_presets(engine: "EffectEngine"):
    """注册 v24 喷泉效果库扩展预设"""
    from .effect_engine import EffectPreset, ParamSpec

    reg = engine._reg

    # ═══════════════════════════════════════════════════════════════════
    #  辅助工具
    # ═══════════════════════════════════════════════════════════════════
    def _clamp(v, lo=0, hi=255):
        return max(lo, min(hi, int(v)))

    def _ease_in_out(t):
        return t * t * (3.0 - 2.0 * t)

    def _triangle(t):
        """三角波 0→1→0, 周期=1"""
        return 1.0 - abs(2.0 * (t % 1.0) - 1.0)

    def _sawtooth(t):
        """锯齿波 0→1, 周期=1"""
        return t % 1.0

    def _square(t, duty=0.5):
        """方波"""
        return 1.0 if (t % 1.0) < duty else 0.0

    # ═══════════════════════════════════════════════════════════════════
    #  A. 灯光(Light)效果预设
    # ═══════════════════════════════════════════════════════════════════

    # --- A1: N组正跑 ---
    def _light_group_chase_fwd(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(1, int(params.get("groups", 4)))
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        grp_size = max(1, n // groups)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp_idx = seq_i // grp_size
            local_phase = (phase - grp_idx / groups) % 1.0
            val = hi if local_phase < (1.0 / groups) else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_group_chase_fwd", name="N组正跑", category="灯光/跑灯",
        description="灯光分N组从左到右依次追逐，参考喷泉灯光四组正跑",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="->",
        tags=["light", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 4, 1, 20, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_group_chase_fwd)

    # --- A2: N组反跑 ---
    def _light_group_chase_rev(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(1, int(params.get("groups", 4)))
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        grp_size = max(1, n // groups)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            rev_i = n - 1 - seq_i
            grp_idx = rev_i // grp_size
            local_phase = (phase - grp_idx / groups) % 1.0
            val = hi if local_phase < (1.0 / groups) else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_group_chase_rev", name="N组反跑", category="灯光/跑灯",
        description="灯光分N组从右到左依次追逐",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="<-",
        tags=["light", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 4, 1, 20, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_group_chase_rev)

    # --- A3: 间N跑 ---
    def _light_skip_chase(ef, t, sorted_fx, fdefs, params, fps):
        skip = max(2, int(params.get("skip", 4)))
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        active = int((t * speed * skip) % skip)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            val = hi if (seq_i % skip) == active else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_skip_chase", name="间N跑", category="灯光/跑灯",
        description="每隔N个灯亮一个，依次移动。参考间四跑/间五跑/间六跑/间八跑",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="..o",
        tags=["light", "skip", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("skip", "间隔数", "int", 4, 2, 16, 1,
                tooltip="控制跳过的步数。值越大设备序列中跳过的间隙越多"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_skip_chase)

    # --- A4: 间N跳跑 ---
    def _light_skip_jump(ef, t, sorted_fx, fdefs, params, fps):
        skip = max(2, int(params.get("skip", 4)))
        speed = float(params.get("speed", 2.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        step = int(t * speed * skip) % (skip * 2)
        active = step % skip
        is_jump = step >= skip
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if is_jump:
                val = hi if (seq_i % skip) != active else lo
            else:
                val = hi if (seq_i % skip) == active else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_skip_jump", name="间N跳跑", category="灯光/跑灯",
        description="间隔跑灯带跳变效果。参考间八跳跑/间六跳跑/间五跳跑/间四跳跑",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="oXo",
        tags=["light", "skip", "jump", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("skip", "间隔数", "int", 4, 2, 16, 1,
                tooltip="控制跳过的步数。值越大设备序列中跳过的间隙越多"),
            ParamSpec("speed", "速度", "float", 2.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_skip_jump)

    # --- A5: 彩色跑 ---
    def _light_color_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        n = len(sorted_fx)
        if n == 0:
            return
        colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255), (255, 255, 255),
        ]
        nc = len(colors)
        offset = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ci = int(offset + seq_i * nc / max(1, n)) % nc
            r, g, b = colors[ci]
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="light_color_chase", name="彩色跑", category="灯光/彩色",
        description="多种颜色依次追逐跑动。参考彩色跑/彩色跑2/黑底彩色跑",
        target_channels=["R", "G", "B"], icon="RGB",
        tags=["light", "color", "chase", "fountain"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _light_color_chase)

    # --- A6: 渐变跑 ---
    def _light_gradient_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        hue_range = float(params.get("hue_range", 1.0))
        n = len(sorted_fx)
        if n == 0:
            return
        offset = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            hue = ((offset + seq_i / max(1, n) * hue_range) % 1.0)
            # HSV to RGB
            h6 = hue * 6.0
            hi_h = int(h6) % 6
            f = h6 - int(h6)
            if hi_h == 0:
                r, g, b = 1.0, f, 0.0
            elif hi_h == 1:
                r, g, b = 1.0 - f, 1.0, 0.0
            elif hi_h == 2:
                r, g, b = 0.0, 1.0, f
            elif hi_h == 3:
                r, g, b = 0.0, 1.0 - f, 1.0
            elif hi_h == 4:
                r, g, b = f, 0.0, 1.0
            else:
                r, g, b = 1.0, 0.0, 1.0 - f
            ef.values[orig_i]["R"] = _clamp(r * 255)
            ef.values[orig_i]["G"] = _clamp(g * 255)
            ef.values[orig_i]["B"] = _clamp(b * 255)

    reg(EffectPreset(
        id="light_gradient_chase", name="渐变跑", category="灯光/彩色",
        description="色相渐变追逐跑动，颜色平滑过渡。参考渐变跑1~4",
        target_channels=["R", "G", "B"], icon="~RG",
        tags=["light", "gradient", "chase", "fountain"],
        params=[
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("hue_range", "色相范围", "float", 1.0, 0.1, 3.0, 0.1,
                tooltip="控制色相的变化范围。值越大色相在色环上扫过的角度越宽"),
        ],
    ), _light_gradient_chase)

    # --- A7: 七彩变化 ---
    def _light_rainbow_change(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        mode = params.get("mode", "同步")
        n = len(sorted_fx)
        if n == 0:
            return
        colors = [
            (255, 0, 0), (255, 127, 0), (255, 255, 0), (0, 255, 0),
            (0, 0, 255), (75, 0, 130), (148, 0, 211),
        ]
        nc = len(colors)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if mode == "同步":
                ci = int(t * speed * nc) % nc
            elif mode == "渐进":
                ci = int(t * speed * nc + seq_i * 0.5) % nc
            else:
                ci = int(t * speed * nc + seq_i) % nc
            r, g, b = colors[ci]
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="light_rainbow_change", name="七彩变化", category="灯光/彩色",
        description="七种颜色依次切换变化。参考七彩变化1~4",
        target_channels=["R", "G", "B"], icon="7C",
        tags=["light", "rainbow", "fountain"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("mode", "模式", "choice", "同步",
                      choices=["同步", "渐进", "交错"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _light_rainbow_change)

    # --- A8: N组变化 ---
    def _light_group_change(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(2, int(params.get("groups", 5)))
        speed = float(params.get("speed", 0.8))
        n = len(sorted_fx)
        if n == 0:
            return
        colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (255, 0, 255), (0, 255, 255), (255, 128, 0),
        ]
        nc = len(colors)
        grp_size = max(1, n // groups)
        step = int(t * speed * groups)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i // grp_size
            ci = (step + grp) % nc
            r, g, b = colors[ci]
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="light_group_change", name="N组变化", category="灯光/彩色",
        description="分N组同时变换颜色。参考五组变化1~4",
        target_channels=["R", "G", "B"], icon="GrC",
        tags=["light", "group", "color", "fountain"],
        params=[
            ParamSpec("groups", "分组数", "int", 5, 2, 20, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _light_group_change)

    # --- A9: 彩色正跑起/反跑起 ---
    def _light_color_chase_start(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        direction = params.get("direction", "正向")
        n = len(sorted_fx)
        if n == 0:
            return
        colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255), (255, 255, 255),
        ]
        nc = len(colors)
        progress = (t * speed) % 1.0
        active_count = max(1, int(progress * n))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            idx = seq_i if direction == "正向" else (n - 1 - seq_i)
            if idx < active_count:
                ci = idx % nc
                r, g, b = colors[ci]
            else:
                r, g, b = 0, 0, 0
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="light_color_chase_start", name="彩色跑起", category="灯光/彩色",
        description="彩色灯从一端逐个点亮。参考彩色正跑起/彩色反跑起",
        target_channels=["R", "G", "B"], icon=">>C",
        tags=["light", "color", "start", "fountain"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "正向",
                      choices=["正向", "反向"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ],
    ), _light_color_chase_start)

    # --- A10: 无色间N跑 ---
    def _light_nocolor_skip(ef, t, sorted_fx, fdefs, params, fps):
        skip = max(2, int(params.get("skip", 3)))
        speed = float(params.get("speed", 1.0))
        exclude = params.get("exclude", "白")
        n = len(sorted_fx)
        if n == 0:
            return
        colors = [
            ("红", 255, 0, 0), ("绿", 0, 255, 0), ("蓝", 0, 0, 255),
            ("黄", 255, 255, 0), ("青", 0, 255, 255), ("紫", 255, 0, 255),
            ("白", 255, 255, 255),
        ]
        filtered = [(r, g, b) for name, r, g, b in colors if name != exclude]
        nc = len(filtered)
        if nc == 0:
            return
        active = int(t * speed * skip) % skip
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if (seq_i % skip) == active:
                ci = (seq_i // skip) % nc
                r, g, b = filtered[ci]
            else:
                r, g, b = 0, 0, 0
            ef.values[orig_i]["R"] = r
            ef.values[orig_i]["G"] = g
            ef.values[orig_i]["B"] = b

    reg(EffectPreset(
        id="light_nocolor_skip", name="无色间N跑", category="灯光/彩色",
        description="排除某种颜色的间隔跑灯。参考无白间三跑/无蓝间三跑等",
        target_channels=["R", "G", "B"], icon="-C",
        tags=["light", "skip", "color", "fountain"],
        params=[
            ParamSpec("skip", "间隔数", "int", 3, 2, 8, 1,
                tooltip="控制跳过的步数。值越大设备序列中跳过的间隙越多"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("exclude", "排除颜色", "choice", "白",
                      choices=["白", "蓝", "红", "绿", "黄", "紫"],
                tooltip="指定被排除不参与效果的颜色通道"),
        ],
    ), _light_nocolor_skip)

    # --- A11: 灯光闪烁 ---
    def _light_strobe(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 4.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        duty = float(params.get("duty", 0.3))
        mode = params.get("mode", "同步")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if mode == "同步":
                phase = t * freq
            elif mode == "交替":
                phase = t * freq + seq_i * 0.5
            else:
                phase = t * freq + seq_i / max(1, n)
            val = hi if (phase % 1.0) < duty else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_strobe_v24", name="灯光闪烁", category="灯光/闪烁",
        description="可调频率和占空比的闪烁效果",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="**",
        tags=["light", "strobe", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq", "频率(Hz)", "float", 4.0, 0.5, 20.0, 0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("duty", "占空比", "float", 0.3, 0.05, 0.95, 0.05,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
            ParamSpec("mode", "模式", "choice", "同步",
                      choices=["同步", "交替", "渐进"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_strobe)

    # --- A12: 灯光正开/反开/边开/中开 ---
    def _light_open(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        mode = params.get("mode", "正开")
        n = len(sorted_fx)
        if n == 0:
            return
        progress = _triangle(t * speed)
        active_count = int(progress * n)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if mode == "正开":
                on = seq_i < active_count
            elif mode == "反开":
                on = (n - 1 - seq_i) < active_count
            elif mode == "中开":
                mid = n // 2
                dist = abs(seq_i - mid)
                on = dist < active_count // 2
            else:  # 边开
                dist = min(seq_i, n - 1 - seq_i)
                on = dist < active_count // 2
            ef.values[orig_i][ch] = _clamp(hi if on else lo)

    reg(EffectPreset(
        id="light_open", name="灯光开合", category="灯光/开合",
        description="灯光正开/反开/边开/中开效果。参考正开/反开/边开/中开",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="<>",
        tags=["light", "open", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("mode", "模式", "choice", "正开",
                      choices=["正开", "反开", "中开", "边开"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_open)

    # ═══════════════════════════════════════════════════════════════════
    #  B. 泵/变频器(Pump)效果预设
    # ═══════════════════════════════════════════════════════════════════

    # --- B1: 矩形跑 ---
    def _pump_rect_chase(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(2, int(params.get("groups", 2)))
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "正跑")
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            idx = seq_i if direction == "正跑" else (n - 1 - seq_i)
            grp = idx // grp_size
            on = ((phase * groups) % groups) >= grp and \
                 ((phase * groups) % groups) < (grp + 1)
            ef.values[orig_i][ch] = _clamp(hi if on else lo)

    reg(EffectPreset(
        id="pump_rect_chase", name="矩形跑", category="泵/跑泉",
        description="变频泵矩形波形跑泉。参考两组矩形正跑/反跑",
        target_channels=["pump", "valve", "dimmer"], icon="[]",
        tags=["pump", "rect", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 2, 1, 10, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "正跑",
                      choices=["正跑", "反跑"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_rect_chase)

    # --- B2: 三角跑 ---
    def _pump_triangle_chase(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(2, int(params.get("groups", 5)))
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "右跑")
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            idx = seq_i if direction == "右跑" else (n - 1 - seq_i)
            grp = idx // grp_size
            local = (idx % grp_size) / max(1, grp_size - 1)
            tri_val = _triangle(local + phase + grp / groups)
            val = lo + tri_val * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_triangle_chase", name="三角跑", category="泵/跑泉",
        description="三角波形跑泉。参考五三角右跑/左跑/四个三角跑",
        target_channels=["pump", "valve", "dimmer"], icon="/\\",
        tags=["pump", "triangle", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 5, 2, 20, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "右跑",
                      choices=["右跑", "左跑"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_triangle_chase)

    # --- B3: 锯齿跑 ---
    def _pump_sawtooth_chase(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(2, int(params.get("groups", 3)))
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "正跑")
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            idx = seq_i if direction == "正跑" else (n - 1 - seq_i)
            grp = idx // grp_size
            local = (idx % grp_size) / max(1, grp_size)
            saw_val = _sawtooth(local + phase + grp / groups)
            val = lo + saw_val * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_sawtooth_chase", name="锯齿跑", category="泵/跑泉",
        description="锯齿波形跑泉。参考三组锯齿正跑/反跑",
        target_channels=["pump", "valve", "dimmer"], icon="/|",
        tags=["pump", "sawtooth", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 3, 2, 20, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "正跑",
                      choices=["正跑", "反跑"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_sawtooth_chase)

    # --- B4: 山型跑 ---
    def _pump_mountain_chase(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(1, int(params.get("groups", 2)))
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i // grp_size
            local = (seq_i % grp_size) / max(1, grp_size - 1)
            mountain = 1.0 - abs(2.0 * local - 1.0)
            wave = _triangle(phase + grp / groups)
            val = lo + mountain * wave * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_mountain_chase", name="山型跑", category="泵/跑泉",
        description="山型波形跑泉，中间高两边低。参考两组山型跑/山型向右跑",
        target_channels=["pump", "valve", "dimmer"], icon="/^\\",
        tags=["pump", "mountain", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 2, 1, 10, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_mountain_chase)

    # --- B5: 波浪跑 ---
    def _pump_wave_chase(ef, t, sorted_fx, fdefs, params, fps):
        waves = max(1, int(params.get("waves", 3)))
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            wave = 0.5 + 0.5 * math.sin(2 * math.pi * (ratio * waves - t * speed))
            val = lo + wave * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_wave_chase", name="波浪跑", category="泵/波浪",
        description="正弦波浪跑泉。参考波浪跑/三波浪/八组波浪高低",
        target_channels=["pump", "valve", "dimmer"], icon="~W",
        tags=["pump", "wave", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("waves", "波浪数", "int", 3, 1, 16, 1,
                tooltip="控制波的数量。值越大同一时刻并存的波峰越多"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_wave_chase)

    # --- B6: 旗帜跑 ---
    def _pump_flag_chase(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(2, int(params.get("groups", 5)))
        speed = float(params.get("speed", 0.4))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "向右")
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i // grp_size
            local = (seq_i % grp_size) / max(1, grp_size - 1)
            if direction == "向右":
                flag = 0.5 + 0.5 * math.sin(2 * math.pi * (grp / groups - phase) + local * 0.5)
            elif direction == "向左":
                flag = 0.5 + 0.5 * math.sin(2 * math.pi * (1.0 - grp / groups - phase) + local * 0.5)
            elif direction == "向两边":
                mid = groups / 2
                dist = abs(grp - mid) / mid
                flag = 0.5 + 0.5 * math.sin(2 * math.pi * (dist - phase) + local * 0.5)
            else:  # 向中间
                mid = groups / 2
                dist = 1.0 - abs(grp - mid) / mid
                flag = 0.5 + 0.5 * math.sin(2 * math.pi * (dist - phase) + local * 0.5)
            val = lo + flag * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_flag_chase", name="旗帜跑", category="泵/波浪",
        description="旗帜飘动效果跑泉。参考五组旗帜跑/向两边旗帜跑/向中间旗帜跑",
        target_channels=["pump", "valve", "dimmer"], icon="~F",
        tags=["pump", "flag", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 5, 2, 20, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.4, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "向右",
                      choices=["向右", "向左", "向两边", "向中间"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_flag_chase)

    # --- B7: 慢升快降 ---
    def _pump_slow_rise_fast_fall(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        rise_ratio = float(params.get("rise_ratio", 0.7))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = (t * speed + seq_i / max(1, n) * 0.3) % 1.0
            if phase < rise_ratio:
                p = phase / rise_ratio
                val = lo + _ease_in_out(p) * (hi - lo)
            else:
                p = (phase - rise_ratio) / (1.0 - rise_ratio)
                val = hi - p * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_slow_rise_fast_fall", name="慢升快降", category="泵/渐变",
        description="缓慢上升快速下降的变频泵效果。参考慢升快降",
        target_channels=["pump", "valve", "dimmer"], icon="/\\|",
        tags=["pump", "rise", "fall", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("rise_ratio", "上升比例", "float", 0.7, 0.1, 0.95, 0.05,
                tooltip="控制上升阶段占周期的比例。值越大攀升过程越缓"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_slow_rise_fast_fall)

    # --- B8: 放射跑 ---
    def _pump_radial_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        mid = n / 2.0
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / mid
            wave = 0.5 + 0.5 * math.sin(2 * math.pi * (dist - phase))
            val = lo + wave * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_radial_chase", name="放射跑", category="泵/跑泉",
        description="从中心向两边放射的跑泉效果。参考放射跑/放射跑2",
        target_channels=["pump", "valve", "dimmer"], icon="<=>",
        tags=["pump", "radial", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_radial_chase)

    # --- B9: 增加跑 ---
    def _pump_incremental(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        progress = (t * speed) % 1.0
        active_count = int(progress * n)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < active_count:
                level = lo + (hi - lo) * (1.0 - seq_i / max(1, n))
            else:
                level = lo
            ef.values[orig_i][ch] = _clamp(level)

    reg(EffectPreset(
        id="pump_incremental", name="增加跑", category="泵/跑泉",
        description="逐个增加的跑泉效果。参考增加跑",
        target_channels=["pump", "valve", "dimmer"], icon="+>",
        tags=["pump", "incremental", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_incremental)

    # --- B10: 向边/中跑 ---
    def _pump_center_edge(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "向边")
        groups = max(1, int(params.get("groups", 1)))
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i // grp_size
            local = (seq_i % grp_size) / max(1, grp_size - 1)
            if direction == "向边":
                dist = abs(2.0 * local - 1.0)
            else:
                dist = 1.0 - abs(2.0 * local - 1.0)
            wave = 0.5 + 0.5 * math.sin(2 * math.pi * (dist - phase + grp * 0.2))
            val = lo + wave * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_center_edge", name="向边/中跑", category="泵/跑泉",
        description="从中间向两边或从两边向中间跑泉。参考一组向边跑/向中跑",
        target_channels=["pump", "valve", "dimmer"], icon="><",
        tags=["pump", "center", "edge", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "向边",
                      choices=["向边", "向中"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("groups", "分组数", "int", 1, 1, 10, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_center_edge)

    # --- B11: 单/双柱跳跑 ---
    def _pump_column_jump(ef, t, sorted_fx, fdefs, params, fps):
        columns = max(1, int(params.get("columns", 1)))
        speed = float(params.get("speed", 2.0))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        step = int(t * speed) % n
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            on = False
            for c in range(columns):
                if seq_i == (step + c * n // max(1, columns)) % n:
                    on = True
                    break
            ef.values[orig_i][ch] = _clamp(hi if on else lo)

    reg(EffectPreset(
        id="pump_column_jump", name="柱跳跑", category="泵/跑泉",
        description="单柱或双柱跳跃式跑泉。参考单柱跳跑/双柱跳跑",
        target_channels=["pump", "valve", "dimmer"], icon="|!",
        tags=["pump", "column", "jump", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("columns", "柱数", "int", 1, 1, 4, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("speed", "速度", "float", 2.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_column_jump)

    # --- B12: 整体慢升 ---
    def _pump_slow_rise_all(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.2))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        phase = _triangle(t * speed)
        val = _clamp(lo + _ease_in_out(phase) * (hi - lo))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="pump_slow_rise_all", name="整体慢升", category="泵/渐变",
        description="所有泵同时缓慢升降。参考整体慢升",
        target_channels=["pump", "valve", "dimmer"], icon="/~",
        tags=["pump", "slow", "rise", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.2, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_slow_rise_all)

    # --- B13: 间二慢升 ---
    def _pump_skip2_slow_rise(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        phase = _triangle(t * speed)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i % 2 == 0:
                val = lo + _ease_in_out(phase) * (hi - lo)
            else:
                val = lo + _ease_in_out(1.0 - phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_skip2_slow_rise", name="间二慢升", category="泵/渐变",
        description="奇偶交替缓慢升降。参考间二慢升/间二慢升2",
        target_channels=["pump", "valve", "dimmer"], icon="oXo",
        tags=["pump", "skip", "slow", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_skip2_slow_rise)

    # --- B14: 天天向上 ---
    def _pump_step_up(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        steps = max(2, int(params.get("steps", 5)))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        current_step = int(phase * steps)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = int(seq_i * steps / n)
            if grp <= current_step:
                level = lo + (hi - lo) * (grp + 1) / steps
            else:
                level = lo
            ef.values[orig_i][ch] = _clamp(level)

    reg(EffectPreset(
        id="pump_step_up", name="天天向上", category="泵/渐变",
        description="阶梯式逐级上升效果。参考天天向上/天天向上5/天天向上6",
        target_channels=["pump", "valve", "dimmer"], icon="/_/",
        tags=["pump", "step", "up", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("steps", "阶梯数", "int", 5, 2, 20, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_step_up)

    # --- B15: 出没效果 ---
    def _pump_appear_disappear(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(2, int(params.get("groups", 6)))
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i // grp_size
            phase = (t * speed + grp / groups) % 1.0
            val = hi if phase < 0.5 else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_appear_disappear", name="出没效果", category="泵/跑泉",
        description="分组交替出现和消失。参考六组出没",
        target_channels=["pump", "valve", "dimmer"], icon="?!",
        tags=["pump", "appear", "disappear", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 6, 2, 20, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_appear_disappear)

    # --- B16: 奇偶对跑 ---
    def _pump_odd_even_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i % 2 == 0:
                wave = 0.5 + 0.5 * math.sin(2 * math.pi * (seq_i / max(1, n) - phase))
            else:
                wave = 0.5 + 0.5 * math.sin(2 * math.pi * (seq_i / max(1, n) + phase))
            val = lo + wave * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_odd_even_chase", name="奇偶对跑", category="泵/跑泉",
        description="奇数偶数泵反向追逐。参考奇偶行对跑/奇偶对跑循环",
        target_channels=["pump", "valve", "dimmer"], icon="<>",
        tags=["pump", "odd", "even", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_odd_even_chase)

    # --- B17: 扇形跑 ---
    def _pump_fan_chase(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(2, int(params.get("groups", 3)))
        speed = float(params.get("speed", 0.4))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i // grp_size
            local = (seq_i % grp_size) / max(1, grp_size - 1)
            fan = 1.0 - abs(2.0 * local - 1.0)
            wave = _triangle(phase + grp / groups)
            val = lo + fan * wave * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_fan_chase", name="扇形跑", category="泵/跑泉",
        description="扇形展开跑泉效果。参考三组扇形跑",
        target_channels=["pump", "valve", "dimmer"], icon="<|>",
        tags=["pump", "fan", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 3, 2, 10, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.4, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_fan_chase)

    # ═══════════════════════════════════════════════════════════════════
    #  C. 阀门(Valve)效果预设
    # ═══════════════════════════════════════════════════════════════════

    # --- C1: 阀门正开/反开/边开/中开 ---
    def _valve_open(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "valve")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        mode = params.get("mode", "正开")
        n = len(sorted_fx)
        if n == 0:
            return
        progress = _triangle(t * speed)
        active_count = max(0, int(progress * n))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if mode == "正开":
                on = seq_i < active_count
            elif mode == "反开":
                on = (n - 1 - seq_i) < active_count
            elif mode == "中开":
                mid = n // 2
                dist = abs(seq_i - mid)
                on = dist < active_count // 2
            else:  # 边开
                dist = min(seq_i, n - 1 - seq_i)
                on = dist < active_count // 2
            ef.values[orig_i][ch] = _clamp(hi if on else lo)

    reg(EffectPreset(
        id="valve_open_v24", name="阀门开合", category="阀门/开合",
        description="阀门正开/反开/边开/中开效果。参考正开/反开/边开/中开",
        target_channels=["valve", "pump", "dimmer"], icon="[>",
        tags=["valve", "open", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve",
                      choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("mode", "模式", "choice", "正开",
                      choices=["正开", "反开", "中开", "边开"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _valve_open)

    # --- C2: 阀门渐开/渐关 ---
    def _valve_gradual(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "valve")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "正渐开")
        n = len(sorted_fx)
        if n == 0:
            return
        phase = _triangle(t * speed)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            if direction == "正渐开":
                delay = ratio * 0.5
            elif direction == "反渐开":
                delay = (1.0 - ratio) * 0.5
            elif direction == "中渐开":
                delay = abs(2.0 * ratio - 1.0) * 0.5
            else:  # 边渐开
                delay = (1.0 - abs(2.0 * ratio - 1.0)) * 0.5
            local_phase = max(0.0, min(1.0, (phase - delay) / max(0.01, 1.0 - delay)))
            val = lo + _ease_in_out(local_phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="valve_gradual", name="阀门渐开", category="阀门/渐变",
        description="阀门渐进式开启。参考正渐开/反渐开/中渐开/边渐开",
        target_channels=["valve", "pump", "dimmer"], icon="~>",
        tags=["valve", "gradual", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve",
                      choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "正渐开",
                      choices=["正渐开", "反渐开", "中渐开", "边渐开"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _valve_gradual)

    # --- C3: 阀门跑一次 ---
    def _valve_run_once(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "valve")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "正跑")
        tail = max(1, int(params.get("tail", 3)))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        head_pos = phase * (n + tail)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            idx = seq_i if direction == "正跑" else (n - 1 - seq_i)
            if direction == "中跑":
                mid = n // 2
                idx = abs(seq_i - mid)
            elif direction == "边跑":
                idx = min(seq_i, n - 1 - seq_i)
            dist = head_pos - idx
            if 0 <= dist < tail:
                brightness = 1.0 - dist / tail
                val = lo + brightness * (hi - lo)
            else:
                val = lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="valve_run_once", name="阀门跑一次", category="阀门/跑泉",
        description="阀门单次跑动效果。参考正跑一次/反跑一次/中跑一次/边跑一次",
        target_channels=["valve", "pump", "dimmer"], icon="->|",
        tags=["valve", "run", "once", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve",
                      choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "正跑",
                      choices=["正跑", "反跑", "中跑", "边跑"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("tail", "拖尾长度", "int", 3, 1, 20, 1,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _valve_run_once)

    # --- C4: 阀门N组正跑/反跑 ---
    def _valve_group_chase(ef, t, sorted_fx, fdefs, params, fps):
        groups = max(1, int(params.get("groups", 3)))
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "valve")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "正跑")
        n = len(sorted_fx)
        if n == 0:
            return
        grp_size = max(1, n // groups)
        phase = (t * speed) % 1.0
        active_grp = int(phase * groups) % groups
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            idx = seq_i if direction == "正跑" else (n - 1 - seq_i)
            grp = idx // grp_size
            ef.values[orig_i][ch] = _clamp(hi if grp == active_grp else lo)

    reg(EffectPreset(
        id="valve_group_chase", name="阀门分组跑", category="阀门/跑泉",
        description="阀门分组追逐效果。参考一组正跑~五组正跑/反跑",
        target_channels=["valve", "pump", "dimmer"], icon="V>",
        tags=["valve", "group", "chase", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve",
                      choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("groups", "分组数", "int", 3, 1, 10, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "正跑",
                      choices=["正跑", "反跑"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _valve_group_chase)

    # --- C5: 半渐跑 ---
    def _valve_half_gradual(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "valve")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        direction = params.get("direction", "正向")
        n = len(sorted_fx)
        if n == 0:
            return
        half = n // 2
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if direction == "正向":
                local_idx = seq_i if seq_i < half else (n - 1 - seq_i)
            elif direction == "反向":
                local_idx = (half - 1 - seq_i) if seq_i < half else (seq_i - half)
            else:
                local_idx = abs(seq_i - half)
            ratio = local_idx / max(1, half - 1)
            delay = ratio * 0.5
            local_phase = max(0.0, min(1.0, (phase - delay) * 2.0))
            val = lo + _ease_in_out(local_phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="valve_half_gradual", name="半渐跑", category="阀门/渐变",
        description="一半渐进跑动效果。参考一半渐跑正一次/反一次/中一半渐跑",
        target_channels=["valve", "pump", "dimmer"], icon="~|",
        tags=["valve", "half", "gradual", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "valve",
                      choices=["valve", "pump", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("direction", "方向", "choice", "正向",
                      choices=["正向", "反向", "中间"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _valve_half_gradual)

    # ═══════════════════════════════════════════════════════════════════
    #  D. 数控电机(Motor)效果预设
    # ═══════════════════════════════════════════════════════════════════

    # --- D1: 龙摆（正向/反向/定位） ---
    def _motor_dragon_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 80.0))
        mode = params.get("mode", "正向")
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            if mode == "正向":
                delay = ratio
            elif mode == "反向":
                delay = 1.0 - ratio
            elif mode == "定位":
                delay = ratio
                angle_range_local = angle_range * (1.0 - ratio * 0.5)
            elif mode == "顺时针":
                delay = ratio * 2
            elif mode == "逆时针":
                delay = (1.0 - ratio) * 2
            else:
                delay = ratio
            angle = math.sin(2 * math.pi * (phase - delay * 0.5))
            ar = angle_range if mode != "定位" else angle_range_local
            val = 128 + angle * ar * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_dragon_swing", name="龙摆", category="电机/龙摆",
        description="数控电机龙摆效果。参考龙摆正向/反向/定位龙摆/顺时针龙摆/逆时针龙摆",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="~D",
        tags=["motor", "dragon", "swing", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 80.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
            ParamSpec("mode", "模式", "choice", "正向",
                      choices=["正向", "反向", "定位", "顺时针", "逆时针"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _motor_dragon_swing)

    # --- D2: 对龙摆 ---
    def _motor_dual_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 80.0))
        n = len(sorted_fx)
        if n == 0:
            return
        half = n // 2
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                ratio = seq_i / max(1, half - 1)
                delay = ratio
            else:
                ratio = (seq_i - half) / max(1, n - half - 1)
                delay = 1.0 - ratio
            angle = math.sin(2 * math.pi * (phase - delay * 0.5))
            if seq_i >= half:
                angle = -angle
            val = 128 + angle * angle_range * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_dual_dragon", name="对龙摆", category="电机/龙摆",
        description="两组对向龙摆效果。参考两组对龙摆1/2/3",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="<D>",
        tags=["motor", "dual", "dragon", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 80.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
        ],
    ), _motor_dual_dragon)

    # --- D3: 旋转 ---
    def _motor_rotation(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.2))
        ch = params.get("ch_type", "motor_x")
        groups = max(1, int(params.get("groups", 1)))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            grp = seq_i * groups // n
            direction = 1 if grp % 2 == 0 else -1
            angle = math.sin(2 * math.pi * (phase * direction + ratio))
            val = 128 + angle * 100 * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_rotation", name="旋转", category="电机/旋转",
        description="数控电机旋转效果。参考一组旋转/二组旋转/十头旋转",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="@R",
        tags=["motor", "rotation", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.2, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups", "分组数", "int", 1, 1, 4, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ],
    ), _motor_rotation)

    # --- D4: 圆摇 ---
    def _motor_circle_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        radius = float(params.get("radius", 40.0))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            delay = ratio * 0.5
            angle_x = math.cos(2 * math.pi * (phase - delay))
            angle_y = math.sin(2 * math.pi * (phase - delay))
            val_x = 128 + angle_x * radius * 255 / 360
            val_y = 128 + angle_y * radius * 255 / 360
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_circle_swing", name="圆摇", category="电机/圆摇",
        description="数控电机圆形摇摆效果。参考圆摇1/2/3",
        target_channels=["motor_x", "motor_y"], icon="@C",
        tags=["motor", "circle", "swing", "fountain"],
        params=[
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius", "半径(度)", "float", 40.0, 5.0, 90.0, 5.0,
                tooltip="控制轨道或图案的半径。值越大运动轨迹的范围越广"),
        ],
    ), _motor_circle_swing)

    # --- D5: 交叉摆 ---
    def _motor_cross_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 60.0))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            direction = 1 if seq_i % 2 == 0 else -1
            angle = math.sin(2 * math.pi * phase) * direction
            val = 128 + angle * angle_range * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_swing", name="交叉摆", category="电机/交叉",
        description="奇偶喷头交叉摆动。参考交叉摆/奇偶顺序交叉",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="X",
        tags=["motor", "cross", "swing", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 60.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
        ],
    ), _motor_cross_swing)

    # --- D6: 开合屏 ---
    def _motor_open_close_screen(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.2))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 80.0))
        mode = params.get("mode", "开屏")
        n = len(sorted_fx)
        if n == 0:
            return
        phase = _triangle(t * speed)
        mid = n / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / mid if mid > 0 else 0
            if mode == "开屏":
                target = ratio * angle_range * phase
            elif mode == "合屏":
                target = ratio * angle_range * (1.0 - phase)
            elif mode == "开合":
                target = ratio * angle_range * phase
            else:  # 抖动
                target = ratio * angle_range * phase + math.sin(t * 10) * 5
            val = 128 + target * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_open_close_screen", name="开合屏", category="电机/开合",
        description="孔雀开合屏效果。参考开屏摆/开屏抖/珊瑚开合",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="<=>",
        tags=["motor", "open", "close", "screen", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.2, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 80.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
            ParamSpec("mode", "模式", "choice", "开屏",
                      choices=["开屏", "合屏", "开合", "抖动"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _motor_open_close_screen)

    # --- D7: 扇形摆 ---
    def _motor_fan_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 90.0))
        mode = params.get("mode", "直角扇形")
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            if mode == "直角扇形":
                base_angle = ratio * angle_range - angle_range / 2
                swing = math.sin(2 * math.pi * phase) * 20
            elif mode == "扇子摆":
                base_angle = ratio * angle_range - angle_range / 2
                swing = math.sin(2 * math.pi * (phase - ratio * 0.3)) * 30
            elif mode == "凤尾摆":
                base_angle = (ratio * 2 - 1) * angle_range / 2
                swing = math.sin(2 * math.pi * (phase - ratio * 0.5)) * 25
            else:  # 凤尾扇子圆摆
                base_angle = ratio * angle_range - angle_range / 2
                swing = math.sin(2 * math.pi * phase) * 20 * math.cos(2 * math.pi * ratio)
            val = 128 + (base_angle + swing) * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_fan_swing", name="扇形摆", category="电机/扇形",
        description="扇形摆动效果。参考直角扇形/扇子摆/凤尾摆/凤尾扇子圆摆",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="<|",
        tags=["motor", "fan", "swing", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 90.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
            ParamSpec("mode", "模式", "choice", "直角扇形",
                      choices=["直角扇形", "扇子摆", "凤尾摆", "凤尾扇子圆摆"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _motor_fan_swing)

    # --- D8: 海鸥摆 ---
    def _motor_seagull_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 60.0))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        mid = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1, mid)
            wing_angle = dist * angle_range
            swing = math.sin(2 * math.pi * (phase - dist * 0.3))
            if seq_i < mid:
                val = 128 + (wing_angle * swing) * 255 / 360
            else:
                val = 128 + (-wing_angle * swing) * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_seagull_swing", name="海鸥摆", category="电机/造型",
        description="海鸥展翅摆动效果。参考海鸥摆/海鸥组合摆",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="~V~",
        tags=["motor", "seagull", "swing", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 60.0, 10.0, 120.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
        ],
    ), _motor_seagull_swing)

    # --- D9: 花型造型 ---
    def _motor_flower(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.2))
        ch = params.get("ch_type", "motor_x")
        petals = max(3, int(params.get("petals", 7)))
        radius = float(params.get("radius", 50.0))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            petal_angle = ratio * 2 * math.pi
            r = radius * abs(math.cos(petals * petal_angle / 2))
            angle = r * math.sin(2 * math.pi * phase + petal_angle)
            val = 128 + angle * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_flower", name="花型造型", category="电机/造型",
        description="花瓣形造型效果。参考七头花/百灵鸟/太阳造型",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="*F",
        tags=["motor", "flower", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.2, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("petals", "花瓣数", "int", 7, 3, 16, 1,
                tooltip="控制花瓣/瓣叶的数量。值越大花瓣越密集"),
            ParamSpec("radius", "半径(度)", "float", 50.0, 10.0, 90.0, 5.0,
                tooltip="控制轨道或图案的半径。值越大运动轨迹的范围越广"),
        ],
    ), _motor_flower)

    # --- D10: 双层摆/内摆 ---
    def _motor_dual_layer(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_outer = float(params.get("angle_outer", 80.0))
        angle_inner = float(params.get("angle_inner", 40.0))
        n = len(sorted_fx)
        if n == 0:
            return
        half = n // 2
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                ratio = seq_i / max(1, half - 1)
                ar = angle_outer
            else:
                ratio = (seq_i - half) / max(1, n - half - 1)
                ar = angle_inner
            angle = math.sin(2 * math.pi * (phase - ratio * 0.5)) * ar
            val = 128 + angle * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_dual_layer", name="双层摆", category="电机/龙摆",
        description="内外双层不同幅度摆动。参考双层摆/内摆",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="=D=",
        tags=["motor", "dual", "layer", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_outer", "外层角度", "float", 80.0, 10.0, 180.0, 5.0,
                tooltip="控制外侧角度。值越大外圈展开越宽"),
            ParamSpec("angle_inner", "内层角度", "float", 40.0, 5.0, 90.0, 5.0,
                tooltip="控制内侧角度。值越大内圈展开越宽"),
        ],
    ), _motor_dual_layer)

    # --- D11: 偶定奇摆/奇定偶摆 ---
    def _motor_odd_even_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 60.0))
        mode = params.get("mode", "偶定奇摆")
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            is_odd = seq_i % 2 == 1
            if mode == "偶定奇摆":
                moving = is_odd
            else:
                moving = not is_odd
            if moving:
                ratio = seq_i / max(1, n - 1)
                angle = math.sin(2 * math.pi * (phase - ratio * 0.5)) * angle_range
            else:
                angle = 0
            val = 128 + angle * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_odd_even_swing", name="奇偶定摆", category="电机/交叉",
        description="奇数或偶数喷头固定，另一组摆动。参考偶定奇摆/奇定偶摆",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="o-o",
        tags=["motor", "odd", "even", "swing", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 60.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
            ParamSpec("mode", "模式", "choice", "偶定奇摆",
                      choices=["偶定奇摆", "奇定偶摆"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _motor_odd_even_swing)

    # --- D12: 对半分开 ---
    def _motor_split_half(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.2))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 80.0))
        n = len(sorted_fx)
        if n == 0:
            return
        half = n // 2
        phase = _triangle(t * speed)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                angle = -angle_range * phase
            else:
                angle = angle_range * phase
            val = 128 + angle * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_split_half", name="对半分开", category="电机/开合",
        description="两半向相反方向分开。参考对半分开",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="<|>",
        tags=["motor", "split", "half", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.2, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 80.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
        ],
    ), _motor_split_half)

    # --- D13: 杨柳摆 ---
    def _motor_willow_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.2))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 50.0))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            wave1 = math.sin(2 * math.pi * (phase - ratio * 0.3))
            wave2 = math.sin(2 * math.pi * (phase * 1.5 - ratio * 0.5)) * 0.3
            angle = (wave1 + wave2) * angle_range * 0.5
            val = 128 + angle * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_willow_swing", name="杨柳摆", category="电机/造型",
        description="柔和的杨柳摆动效果。参考杨柳摆1/2",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="~W~",
        tags=["motor", "willow", "swing", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.2, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 50.0, 10.0, 120.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
        ],
    ), _motor_willow_swing)

    # --- D14: 小幅龙摆 ---
    def _motor_small_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 20.0))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            angle = math.sin(2 * math.pi * (phase - ratio * 0.5)) * angle_range
            val = 128 + angle * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_small_dragon", name="小幅龙摆", category="电机/龙摆",
        description="小幅度快速龙摆。参考小幅龙摆",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="~d",
        tags=["motor", "small", "dragon", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 20.0, 5.0, 60.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
        ],
    ), _motor_small_dragon)

    # --- D15: 时间动作（延时序列） ---
    def _motor_time_action(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "motor_x")
        angle_range = float(params.get("angle_range", 60.0))
        delay_factor = float(params.get("delay_factor", 0.5))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            delay = ratio * delay_factor
            local_t = max(0.0, phase - delay)
            angle = math.sin(2 * math.pi * local_t) * angle_range
            val = 128 + angle * 255 / 360
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_time_action", name="时间动作", category="电机/延时",
        description="带延时的序列动作效果。参考时间动作1~10",
        target_channels=["motor_x", "motor_y", "motor_z"], icon="t>",
        tags=["motor", "time", "action", "fountain"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle_range", "角度范围", "float", 60.0, 10.0, 180.0, 5.0,
                tooltip="控制角度的变化范围。值越大角度摆动的幅度越宽"),
            ParamSpec("delay_factor", "延时系数", "float", 0.5, 0.0, 2.0, 0.1,
                tooltip="控制延迟倍率因子。值越大延迟随设备序号累积越快"),
        ],
    ), _motor_time_action)

    # --- D16: N头造型 ---
    def _motor_multi_head_shape(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.2))
        heads = max(3, int(params.get("heads", 7)))
        radius = float(params.get("radius", 40.0))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = t * speed
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            head_idx = seq_i % heads
            head_angle = head_idx * 2 * math.pi / heads
            r = radius * (0.7 + 0.3 * math.sin(2 * math.pi * phase + head_angle))
            angle_x = r * math.cos(head_angle + phase * 2 * math.pi * 0.1)
            angle_y = r * math.sin(head_angle + phase * 2 * math.pi * 0.1)
            ef.values[orig_i]["motor_x"] = _clamp(128 + angle_x * 255 / 360)
            ef.values[orig_i]["motor_y"] = _clamp(128 + angle_y * 255 / 360)

    reg(EffectPreset(
        id="motor_multi_head_shape", name="N头造型", category="电机/造型",
        description="多头造型效果。参考三头摆/五头造型/六头造型/七头造型",
        target_channels=["motor_x", "motor_y"], icon="*N",
        tags=["motor", "multi", "head", "shape", "fountain"],
        params=[
            ParamSpec("speed", "速度", "float", 0.2, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("heads", "头数", "int", 7, 3, 16, 1,
                tooltip="控制多头/喷头的数量。值越大并行的喷头越多"),
            ParamSpec("radius", "半径(度)", "float", 40.0, 10.0, 90.0, 5.0,
                tooltip="控制轨道或图案的半径。值越大运动轨迹的范围越广"),
        ],
    ), _motor_multi_head_shape)
