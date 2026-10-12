# -*- coding: utf-8 -*-
"""
effect_presets_curated_data — 精炼效果库的家族数据表（全手工编目）

每个条目 = 一条真实效果：传神中文名 + 中文说明 + 手调参数 + Tabler 图标 + 基础/高级分级。
变体之间在 波形/速度/宽窄/颜色/方向/缓动 上有实质观感差异，绝非机械填充。
重心：通用(channel-agnostic) + 灯效。
"""
from __future__ import annotations
from .effect_presets_curated import (
    _scalar_params, _color_params, _motor_params, _fountain_params,
    _formation_params, _formseq_params, _arraycov_params, _seqprog_params,
    _pumpform_params, _valvegate_params, _watercombo_params, _watershow_params,
    _audio_light_params, _audio_color_params, _audio_motor_params,
    _audio_valve_params, _audio_pump_params,
    ParamSpec, CH_TYPES, RGB,
)


def _e(eid, name, cat, desc, kernel, variant, channels, icon, tier, params):
    return dict(id=eid, name=name, category=cat, desc=desc, kernel=kernel,
                variant=variant, channels=channels, icon=icon,
                tags=["curated", tier], params=params)


def build_families():
    F = []
    add = F.append
    ALL = CH_TYPES

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 波形 (channel-agnostic)
    # ════════════════════════════════════════════════════════════════════
    # (波形shape, 中文基名, 图标, compute variant)
    waves = [
        ("sine", "正弦", "wave-sine"), ("tri", "三角", "triangle"),
        ("saw_up", "上升锯齿", "wave-saw-tool"), ("saw_down", "下降锯齿", "wave-saw-tool"),
        ("square", "方波", "square"), ("pulse", "脉冲", "grip-horizontal"),
        ("expo", "指数升", "trending-up"), ("log", "对数升", "trending-up"),
        ("bounce", "弹跳", "arrow-bounce"), ("fold", "折叠", "fold"),
        ("notch", "缺口", "rectangular-prism"), ("twin", "双峰", "wave-sine"),
    ]
    # 每种波形 1 个预设（速度/频率是运行时参数，不拆独立效果）
    wave_roles = [
        ("", "标准波形", dict(speed=1.0, spatial_freq=1.0), "基础"),
    ]
    for shape, zh, icon in waves:
        for suf, d, ov, tier in wave_roles:
            p = _scalar_params(speed=ov["speed"], freq=ov["spatial_freq"], ease=True)
            add(_e(f"cur_wave_{shape}{suf2(suf)}", f"{zh}波{suf}", "通用/波形",
                   f"{zh}波形，{d}，可套任意通道。", "scalar", shape, ALL, icon, tier, p))

    # 叠加/谐波/拍频/回声（高级波形）
    overlays = [
        ("twin", "双频拍", "拍频", "两相近频叠加形成缓慢拍动", "wave-sine", dict(speed=0.8, spatial_freq=2.0), "高级"),
        ("fold", "谐波折叠", "折叠涌动", "折叠波叠加，纹理丰富", "fold", dict(speed=1.2, spatial_freq=3.0), "高级"),
        ("notch", "梳齿门", "梳状", "等间隔缺口梳齿", "rectangular-prism", dict(speed=1.0, spatial_freq=6.0), "中级"),
        ("bounce", "回声衰减", "回声", "主脉冲后多级递减", "wave-sine", dict(speed=1.5, spatial_freq=1.0), "中级"),
    ]
    for shape, base, suf, d, icon, ov, tier in overlays:
        p = _scalar_params(speed=ov["speed"], freq=ov["spatial_freq"], ease=True)
        add(_e(f"cur_ov_{shape}", f"{base}", "通用/波形", d, "scalar", shape, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 包络·动态
    # ════════════════════════════════════════════════════════════════════
    envs = [
        ("adsr", "ADSR包络·打击", "打击型快起快落包络", "adjustments-bolt", dict(attack=0.02, decay=0.15, speed=2.0), "中级"),
        ("adsr", "ADSR包络·弦乐", "弦乐型慢起慢落包络", "adjustments", dict(attack=0.3, decay=0.4, speed=0.6), "中级"),
        ("adsr", "ADSR包络·管风琴", "持续保持型包络", "adjustments", dict(attack=0.1, decay=0.1, speed=0.8), "中级"),
        ("sh", "采样保持·慢", "随机阶梯保持，慢变", "stairs", dict(speed=1.0), "中级"),
        ("sh", "采样保持·快", "随机阶梯保持，快变", "stairs", dict(speed=4.0), "中级"),
        ("step", "四级阶梯", "四级离散台阶", "stairs-up", dict(steps=4, speed=0.8), "基础"),
        ("step", "八级阶梯", "八级精细台阶", "stairs-up", dict(steps=8, speed=0.8), "基础"),
        ("ramp_hold", "斜坡保持", "升到顶保持再瞬归零", "arrow-ramp-right", dict(speed=0.8), "基础"),
        ("slew", "斜率限速", "柔化跳变的限速正弦", "line-height", dict(speed=1.0), "中级"),
    ]
    for variant, name, d, icon, ov, tier in envs:
        p = [
            ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", ov.get("speed", 1.0), 0.0, 16.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("attack", "起音", "float", ov.get("attack", 0.1), 0.0, 1.0, 0.01,
                tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
            ParamSpec("decay", "衰减", "float", ov.get("decay", 0.3), 0.0, 1.0, 0.01,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
            ParamSpec("steps", "级数", "int", ov.get("steps", 4), 2, 16, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
                tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
            ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        add(_e(f"cur_env_{variant}_{suf2(name)}", name, "通用/渐变", d, "env", variant, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 跑动·扫描
    # ════════════════════════════════════════════════════════════════════
    chases = [
        ("comet", "彗星拖尾", "单点带衰减拖尾", "comet", dict(width=0.12, tail=0.6), "基础"),
        ("comet", "长尾彗星", "超长拖尾", "comet", dict(width=0.25, tail=0.9), "中级"),
        ("dot", "单点跑", "单点硬跑", "point", dict(width=0.08), "基础"),
        ("scan", "双向扫描", "往返扫描光带", "scan", dict(width=0.1), "基础"),
        ("wipe", "擦除填充", "渐进填充擦除", "eraser", dict(width=0.1), "基础"),
        ("wave-saw-tool", "蛇形队列", "柔边蛇形推进", "wave-saw-tool", dict(width=0.15), "中级"),
        ("bounce", "弹跳跑点", "重力弹跳", "arrow-bounce", dict(width=0.12), "中级"),
    ]
    chase_dirs = [("", "正向")]  # 方向是运行时 choice 参数
    for variant, base, d, icon, ov, tier in chases:
        for dsuf, dval in chase_dirs:
            p = [
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 1.0, 0.0, 12.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度", "float", ov.get("width", 0.12), 0.01, 1.0, 0.01,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("tail", "拖尾", "float", ov.get("tail", 0.5), 0.0, 1.0, 0.02,
                    tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
                ParamSpec("direction", "方向", "choice", dval, choices=["正向", "反向", "中心向外", "两侧向中"],
                    tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            add(_e(f"cur_chase_{variant}{suf2(dsuf)}", f"{base}{dsuf}", "通用/跑灯",
                   f"{d}（{dval}）。", "chase", variant, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 随机·噪声
    # ════════════════════════════════════════════════════════════════════
    rands = [
        ("sparkle", "随机火花·稀", "稀疏随机闪点", "sparkles", dict(density=0.15), "基础"),
        ("sparkle", "随机火花·密", "密集随机闪点", "sparkles", dict(density=0.45), "基础"),
        ("twinkle", "星星微闪", "柔和稀疏微闪", "stars", dict(density=0.3), "基础"),
        ("jitter", "抖动闪", "高频小幅抖动", "activity", dict(density=0.4), "基础"),
        ("perlin", "柏林漂移·慢", "有机缓变随机场", "wave-saw-tool", dict(speed=0.4), "中级"),
        ("perlin", "柏林漂移·快", "有机快变随机场", "wave-saw-tool", dict(speed=1.8), "中级"),
    ]
    for variant, name, d, icon, ov, tier in rands:
        p = [
            ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", ov.get("speed", 1.0), 0.0, 12.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("density", "密度", "float", ov.get("density", 0.3), 0.0, 1.0, 0.02,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        add(_e(f"cur_rand_{variant}_{suf2(name)}", name, "通用/渐变", d, "rand", variant, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 呼吸·心跳
    # ════════════════════════════════════════════════════════════════════
    breathes = [
        ("breathe", "柔呼吸·慢", "极柔慢呼吸", "lungs", dict(speed=0.25), "基础"),
        ("breathe", "柔呼吸·快", "明快呼吸", "lungs", dict(speed=0.8), "基础"),
        ("breathe", "错相呼吸", "设备间相位错开呼吸", "lungs", dict(speed=0.4, spread=0.6), "中级"),
        ("heartbeat", "心跳双击", "强弱双拍循环", "heartbeat", dict(speed=1.0), "基础"),
        ("tide", "潮汐脉动", "主副波叠加的潮汐感", "ripple", dict(speed=0.3), "中级"),
    ]
    for variant, name, d, icon, ov, tier in breathes:
        p = [
            ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", ov.get("speed", 0.5), 0.0, 6.0, 0.02,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("spread", "相位展开", "float", ov.get("spread", 0.0), 0.0, 2.0, 0.05,
                tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        add(_e(f"cur_breathe_{variant}_{suf2(name)}", name, "通用/渐变", d, "breathe", variant, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / 颜色（重心）
    # ════════════════════════════════════════════════════════════════════
    colors = [
        ("rainbow", "渐变彩虹", "全色相沿设备铺开滚动", "rainbow", dict(speed=0.3, spatial_freq=1.0), "基础"),
        ("rainbow", "密集彩虹", "高空间频率密集彩虹", "rainbow", dict(speed=0.3, spatial_freq=3.0), "基础"),
        ("hue_rot", "色调旋转", "整体色相匀速旋转", "color-swatch", dict(speed=0.4), "基础"),
        ("colortemp", "色温偏移", "暖白↔冷白循环", "temperature", dict(speed=0.3), "中级"),
        ("warmcool", "冷暖交织", "冷暖双色交替", "temperature", dict(speed=0.4), "中级"),
        ("plasma", "等离子色", "多正弦色场湍流融合", "atom", dict(speed=0.6, spatial_freq=2.0), "高级"),
        ("perlin_color", "柏林噪声色", "有机缓变色场", "wave-saw-tool", dict(speed=0.4), "高级"),
        ("fire", "火焰色", "红橙黄暖色火焰闪动", "flame", dict(speed=1.0), "基础"),
    ]
    for variant, name, d, icon, ov, tier in colors:
        p = _color_params(speed=ov.get("speed", 0.3), freq=ov.get("spatial_freq", 1.0))
        if variant == "colortemp":
            p += [ParamSpec("k_lo", "低色温K", "int", 2700, 1000, 12000, 100,
                tooltip="控制色温范围的下限（开尔文）。值越低色温越暖偏红"),
                  ParamSpec("k_hi", "高色温K", "int", 6500, 1000, 12000, 100,
                      tooltip="控制色温范围的上限（开尔文）。值越高色温越冷偏蓝")]
        add(_e(f"cur_color_{variant}_{suf2(name)}", name, "灯光/颜色", d, "color", variant, RGB, icon, tier, p))

    # 颜色方向变体（彩虹/色调 × 方向）
    for variant, base, icon in [("rainbow", "彩虹", "rainbow"), ("hue_rot", "色环", "color-swatch")]:
        for dsuf, sp in [("", 1.0)]:  # spread is a runtime param
            p = _color_params(speed=0.4, freq=1.0)
            for ps in p:
                if ps.key == "spread":
                    ps.default = abs(sp)
            add(_e(f"cur_colordir_{variant}{suf2(dsuf)}", f"{base}{dsuf}", "灯光/颜色",
                   f"{base}沿方向{dsuf[1:]}。", "color", variant, RGB, icon, "中级", p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / 亮度（标量套 dimmer，命名为灯光语义）
    # ════════════════════════════════════════════════════════════════════
    light_bright = [
        ("sine", "正弦亮度起伏", "wave-sine", dict(speed=0.6, spatial_freq=1.0), "基础"),
        ("tri", "三角明暗", "triangle", dict(speed=0.8, spatial_freq=1.0), "基础"),
        ("saw_up", "线性渐亮", "trending-up", dict(speed=0.5, spatial_freq=0.0), "基础"),
        ("saw_down", "线性渐暗", "trending-down", dict(speed=0.5, spatial_freq=0.0), "基础"),
        ("expo", "软启动渐亮", "trending-up", dict(speed=0.5), "基础"),
        ("bounce", "弹跳亮度", "arrow-bounce", dict(speed=1.2), "中级"),
        ("twin", "双峰亮度波", "wave-sine", dict(speed=0.7, spatial_freq=2.0), "中级"),
        ("fold", "折叠明暗", "fold", dict(speed=1.0, spatial_freq=2.0), "中级"),
    ]
    for shape, name, icon, ov, tier in light_bright:
        p = _scalar_params(speed=ov.get("speed", 0.6), freq=ov.get("spatial_freq", 1.0), ease=True, ch="dimmer")
        add(_e(f"cur_lb_{shape}_{suf2(name)}", name, "灯光/亮度",
               f"{name}，作用于调光。", "scalar", shape, ["dimmer"], icon, tier, p))

    # 灯光 / 跑灯
    light_chase = [
        ("comet", "灯光彗星拖尾", "comet", dict(width=0.12, tail=0.6), "基础"),
        ("scan", "扫描光刃", "scan", dict(width=0.08), "基础"),
        ("dot", "单点跑灯", "point", dict(width=0.08), "基础"),
        ("wave-saw-tool", "蛇形跑灯", "wave-saw-tool", dict(width=0.15), "中级"),
        ("wipe", "渐进擦除", "eraser", dict(width=0.1), "基础"),
    ]
    for variant, base, icon, ov, tier in light_chase:
        for dsuf, dval in [("", "正向")]:  # direction is runtime choice param
            p = [
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=["dimmer", "R", "G", "B", "W"],
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 1.5, 0.0, 12.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度", "float", ov.get("width", 0.12), 0.01, 1.0, 0.01,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("tail", "拖尾", "float", ov.get("tail", 0.5), 0.0, 1.0, 0.02,
                    tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
                ParamSpec("direction", "方向", "choice", dval, choices=["正向", "反向", "中心向外", "两侧向中"],
                    tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            add(_e(f"cur_lc_{variant}{suf2(dsuf)}", f"{base}{dsuf}", "灯光/跑灯",
                   f"{base}（{dval}）。", "chase", variant, ["dimmer"], icon, tier, p))

    # 灯光 / 闪烁
    light_flick = [
        ("sparkle", "空间随机频闪·稀", "sparkles", dict(density=0.15, speed=3.0), "基础"),
        ("sparkle", "空间随机频闪·密", "sparkles", dict(density=0.5, speed=4.0), "基础"),
        ("twinkle", "星闪", "stars", dict(density=0.3, speed=1.5), "基础"),
        ("jitter", "抽搐闪", "bolt", dict(density=0.5, speed=6.0), "中级"),
    ]
    for variant, name, icon, ov, tier in light_flick:
        p = [
            ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", ov.get("speed", 3.0), 0.0, 16.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("density", "密度", "float", ov.get("density", 0.3), 0.0, 1.0, 0.02,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        add(_e(f"cur_lf_{variant}_{suf2(name)}", name, "灯光/闪烁", f"{name}。", "rand", variant, ["dimmer"], icon, tier, p))

    # 灯光 / 氛围（仅灯模拟，无设备）
    ambi = [
        ("fire", "烛光", "慢有机暖色烛焰", "flame", "基础"),
        ("fire", "篝火", "明亮跳动篝火", "flame", "基础"),
        ("perlin_color", "极光幕", "缓慢流动冷色幕", "sparkles", "中级"),
        ("plasma", "水光粼", "水面反光抖动", "ripple", "中级"),
        ("hue_rot", "梦幻流光", "缓慢流动色光", "sparkles", "中级"),
        ("warmcool", "晨曦黄昏", "冷暖渐变天色", "sunrise", "中级"),
    ]
    for variant, name, d, icon, tier in ambi:
        p = _color_params(speed=0.3, freq=1.0)
        add(_e(f"cur_amb_{variant}_{suf2(name)}", name, "灯光/氛围", d, "color", variant, RGB, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 电机 / 轨迹（替代越界的摇头灯类）
    # ════════════════════════════════════════════════════════════════════
    motors = [
        ("circle", "圆形轨迹", "sinX+cosY 画圆", "circle-dot", dict(speed=0.5, amplitude=1.0), "基础"),
        ("ellipse", "椭圆轨迹", "横向扁圆", "oval-vertical", dict(speed=0.5, amplitude=1.0), "基础"),
        ("fig8", "八字轨迹", "无穷符路径", "infinity", dict(speed=0.5), "基础"),
        ("lissajous", "李萨如3:4", "复杂参数曲线", "vector-spline", dict(speed=0.4, freq_x=3, freq_y=4), "高级"),
        ("lissajous", "李萨如2:3", "经典李萨如", "vector-spline", dict(speed=0.4, freq_x=2, freq_y=3), "高级"),
        ("spiral", "螺旋轨迹", "半径张缩螺旋", "spiral", dict(speed=0.4), "中级"),
        ("rose", "花瓣玫瑰线", "玫瑰花瓣路径", "flower", dict(speed=0.4, freq_x=5), "高级"),
        ("sweep", "弧形扫描", "单轴角域扫描", "wave-saw-tool", dict(speed=0.6), "基础"),
        ("ballyhoo", "搜索摆", "大幅类随机搜索", "arrows-shuffle", dict(speed=0.8), "基础"),
        ("pingpong", "往复摆", "两端往返", "arrow-bounce", dict(speed=0.6), "基础"),
    ]
    for variant, name, d, icon, ov, tier in motors:
        p = _motor_params(speed=ov.get("speed", 0.5), amp=ov.get("amplitude", 1.0),
                          fx=ov.get("freq_x", 1.0), fy=ov.get("freq_y", 1.0))
        add(_e(f"cur_motor_{variant}_{suf2(name)}", name, "电机/轨迹", d, "motor", variant,
               ["motor_x", "motor_y"], icon, tier, p))
        # 扇形相位展开变体
        p2 = _motor_params(speed=ov.get("speed", 0.5), amp=ov.get("amplitude", 1.0),
                           fx=ov.get("freq_x", 1.0), fy=ov.get("freq_y", 1.0))
        for ps in p2:
            if ps.key == "spread":
                ps.default = 0.6
        add(_e(f"cur_motor_{variant}_fan", f"{name}·扇展", "电机/轨迹",
               f"{name}，设备间相位展开成扇。", "motor", variant,
               ["motor_x", "motor_y"], icon, "中级", p2))

    # ════════════════════════════════════════════════════════════════════
    # 水景 / 喷射·跑泉·摆喷
    # ════════════════════════════════════════════════════════════════════
    fountains = [
        ("burst", "间歇泉爆发", "节拍全高喷射", "arrow-big-up", "pump", "基础"),
        ("burst", "礼炮齐射", "全体同时爆发", "arrows-up", "valve", "基础"),
        ("supershoot", "超级射流", "单点慢起高喷爆收", "arrow-up-bar", "pump", "高级"),
        ("chase", "跑泉链", "沿次序逐喷拖尾", "arrow-right-bar", "valve", "基础"),
        ("crown", "皇冠喷", "环形齐起伏成冠", "crown", "valve", "基础"),
        ("willow", "垂柳", "中心高外缘渐落", "plant-2", "pump", "中级"),
        ("bloom", "绽放扩散", "中心向外环扩散", "ripple", "valve", "中级"),
        ("cross", "交叉射流", "奇偶对射", "arrows-cross", "valve", "中级"),
        ("wave", "波浪跑泉", "水面波浪起伏", "wave-sine", "pump", "基础"),
    ]
    for variant, name, d, icon, ch, tier in fountains:
        p = _fountain_params(ch=ch)
        add(_e(f"cur_fnt_{variant}_{suf2(name)}", name, "水景/喷射", d, "fountain", variant,
               ["pump", "valve"], icon, tier, p))
        # 方向变体

    # 阀门专属（开合/跑动/波浪）
    valves = [
        ("burst", "阀门快开合", "valve 快速开合", "windmill", dict(speed=2.0), "基础"),
        ("burst", "阀门慢开合", "valve 缓慢开合", "windmill", dict(speed=0.5), "基础"),
        ("chase", "阀门跑动", "valve 逐个开启", "arrow-right-bar", dict(speed=1.0), "基础"),
        ("wave", "阀门波浪", "valve 波浪起伏", "wave-sine", dict(speed=1.0), "中级"),
    ]
    for variant, name, d, icon, ov, tier in valves:
        p = _fountain_params(ch="valve", speed=ov.get("speed", 1.0))
        add(_e(f"cur_valve_{variant}_{suf2(name)}", name, "水景/阀门", d, "fountain", variant,
               ["valve"], icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 波形 — 反相 & 错相角色（实质观感差异）
    # ════════════════════════════════════════════════════════════════════
    wave_roles2 = [
        ("·反相", "高低反转，暗部走动", dict(speed=1.0, spatial_freq=1.0, lo=255, hi=0), "基础"),
        ("·错相展开", "设备间相位错开成行进波", dict(speed=1.0, spatial_freq=2.0), "中级"),
        ("·脉冲叠加", "波形上叠加快速脉冲纹理", dict(speed=3.0, spatial_freq=1.0), "中级"),
    ]
    for shape, zh, icon in waves:
        for suf, d, ov, tier in wave_roles2:
            p = _scalar_params(speed=ov["speed"], freq=ov["spatial_freq"],
                               lo=ov.get("lo", 0), hi=ov.get("hi", 255), ease=True)
            add(_e(f"cur_wave2_{shape}{suf2(suf)}", f"{zh}波{suf}", "通用/波形",
                   f"{zh}波形，{d}。", "scalar", shape, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 脉冲·门控（square/pulse 占空变体）
    # ════════════════════════════════════════════════════════════════════
    gates = [
        ("square", "方波门·均分", "rectangular-prism", dict(duty=0.5, speed=1.5), "基础"),
        ("square", "方波门·窄开", "rectangular-prism", dict(duty=0.2, speed=1.5), "基础"),
        ("square", "方波门·宽开", "rectangular-prism", dict(duty=0.8, speed=1.5), "基础"),
        ("pulse", "脉冲列·密", "grip-horizontal", dict(duty=0.3, speed=3.0), "基础"),
        ("pulse", "脉冲列·疏", "grip-horizontal", dict(duty=0.15, speed=1.2), "基础"),
        ("pulse", "群脉冲", "grip-horizontal", dict(duty=0.5, speed=2.0), "中级"),
        ("notch", "间歇门", "rectangular-prism", dict(speed=1.0, spatial_freq=4.0), "中级"),
    ]
    for shape, name, icon, ov, tier in gates:
        p = _scalar_params(speed=ov.get("speed", 1.5), freq=ov.get("spatial_freq", 1.0),
                           duty=ov.get("duty", 0.5))
        # 加 duty 参数
        p.insert(3, ParamSpec("duty", "占空", "float", ov.get("duty", 0.5), 0.01, 1.0, 0.01,
            tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"))
        add(_e(f"cur_gate_{shape}_{suf2(name)}", name, "通用/波形", f"{name}，可套任意通道。",
               "scalar", shape, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / 颜色 — 扩展色彩模式（重心）
    # ════════════════════════════════════════════════════════════════════
    colors2 = [
        ("dualtone", "双色交替", "两色相位交替", "color-swatch", dict(speed=0.5), "基础"),
        ("tricolor", "三色轮换", "三主色循环切换", "palette", dict(speed=0.4), "基础"),
        ("colorpulse", "颜色脉冲", "色相滚动+亮度脉冲", "color-swatch", dict(speed=0.6), "中级"),
        ("colorwave", "颜色波浪", "波浪式色相起伏", "wave-sine", dict(speed=0.5, spatial_freq=1.5), "中级"),
        ("starfield", "星空色点", "随机彩色星点闪烁", "stars", dict(speed=1.0), "中级"),
        ("sparkles", "极光色", "缓慢流动冷色极光", "sparkles", dict(speed=0.4), "高级"),
        ("ocean", "海洋波光", "蓝绿海洋波光", "ripple", dict(speed=0.5, spatial_freq=1.0), "中级"),
    ]
    for variant, name, d, icon, ov, tier in colors2:
        p = _color_params(speed=ov.get("speed", 0.4), freq=ov.get("spatial_freq", 1.0))
        if variant == "dualtone":
            p += [ParamSpec("hue_a", "色A", "float", 0.0, 0.0, 1.0, 0.01,
                tooltip="控制第一种颜色的色相。0~360 度覆盖完整色环"),
                  ParamSpec("hue_b", "色B", "float", 0.55, 0.0, 1.0, 0.01,
                      tooltip="控制第二种颜色的色相。0~360 度覆盖完整色环")]
        add(_e(f"cur_color2_{variant}_{suf2(name)}", name, "灯光/颜色", d, "color", variant, RGB, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / 亮度 — 阶梯与脉冲套灯（补足灯效数量）
    # ════════════════════════════════════════════════════════════════════
    light_env = [
        ("step", "四级亮度阶梯", "stairs-up", dict(steps=4, speed=0.8), "基础"),
        ("step", "八级亮度阶梯", "stairs-up", dict(steps=8, speed=0.8), "基础"),
        ("adsr", "脉冲群亮度", "adjustments-bolt", dict(attack=0.05, decay=0.2, speed=1.5), "中级"),
        ("ramp_hold", "斜坡保持亮度", "arrow-ramp-right", dict(speed=0.7), "基础"),
        ("slew", "柔启亮度", "line-height", dict(speed=0.8), "中级"),
        ("sh", "随机阶梯亮度", "stairs", dict(speed=1.5), "中级"),
    ]
    for variant, name, icon, ov, tier in light_env:
        p = [
            ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", ov.get("speed", 1.0), 0.0, 12.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("attack", "起音", "float", ov.get("attack", 0.1), 0.0, 1.0, 0.01,
                tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
            ParamSpec("decay", "衰减", "float", ov.get("decay", 0.3), 0.0, 1.0, 0.01,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
            ParamSpec("steps", "级数", "int", ov.get("steps", 4), 2, 16, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
                tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
            ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        add(_e(f"cur_le_{variant}_{suf2(name)}", name, "灯光/亮度", f"{name}。", "env", variant, ["dimmer"], icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 电机 / 轨迹 — 新增路径
    # ════════════════════════════════════════════════════════════════════
    motors2 = [
        ("square_path", "方形轨迹", "走方形路径", "square-rotated", dict(speed=0.4), "中级"),
        ("star", "星形轨迹", "多角星路径", "star", dict(speed=0.4, freq_x=5), "高级"),
        ("precess", "进动圆", "圆心缓慢漂移的圆", "atom", dict(speed=0.5), "高级"),
        ("spiral_up", "螺旋上升摆", "摆动同时半径渐扩", "spiral", dict(speed=0.4), "高级"),
        ("rwalk", "随机游走定位", "限步平滑游走", "route", dict(speed=0.6), "中级"),
    ]
    for variant, name, d, icon, ov, tier in motors2:
        p = _motor_params(speed=ov.get("speed", 0.5), fx=ov.get("freq_x", 1.0))
        add(_e(f"cur_motor2_{variant}_{suf2(name)}", name, "电机/轨迹", d, "motor", variant,
               ["motor_x", "motor_y"], icon, tier, p))
        p2 = _motor_params(speed=ov.get("speed", 0.5), fx=ov.get("freq_x", 1.0))
        for ps in p2:
            if ps.key == "spread":
                ps.default = 0.6
        add(_e(f"cur_motor2_{variant}_fan", f"{name}·扇展", "电机/轨迹",
               f"{name}，相位展开成扇。", "motor", variant, ["motor_x", "motor_y"], icon, "中级", p2))

    # ════════════════════════════════════════════════════════════════════
    # 水景 — 新增喷型
    # ════════════════════════════════════════════════════════════════════
    fountains2 = [
        ("spiral_arr", "螺旋阵列", "多喷错相成行进螺旋", "rotate-clockwise", "pump", "高级"),
        ("jumping", "跳泉", "定向抛物跳跃", "arrow-curve-right", "valve", "高级"),
        ("fan", "扇形展开", "扇面依次展开", "wind", "pump", "中级"),
        ("wall", "水幕脉动", "宽幅水帘起伏", "wall", "pump", "中级"),
        ("tide", "呼吸潮汐", "水位呼吸潮汐", "ripple", "pump", "基础"),
        ("step_water", "阶梯水位", "离散台阶水位", "stairs-up", "pump", "基础"),
    ]
    for variant, name, d, icon, ch, tier in fountains2:
        p = _fountain_params(ch=ch)
        add(_e(f"cur_fnt2_{variant}_{suf2(name)}", name, "水景/喷射", d, "fountain", variant,
               ["pump", "valve"], icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 水景 / 泵纹理 — 波形直接驱动泵（不同波形=不同水形运动）
    # ════════════════════════════════════════════════════════════════════
    pump_tex = [
        ("sine", "泵正弦涌动", "wave-sine"), ("tri", "泵三角起伏", "triangle"),
        ("saw_up", "泵渐涨", "trending-up"), ("saw_down", "泵渐落", "trending-down"),
        ("bounce", "泵弹涌", "arrow-bounce"), ("expo", "泵爆涨", "trending-up"),
        ("twin", "泵双峰涌", "wave-sine"), ("fold", "泵折涌", "fold"),
    ]
    for shape, name, icon in pump_tex:
        p = _scalar_params(speed=0.8, freq=1.0, ease=True, ch="pump")
        p[0] = ParamSpec("ch_type", "通道类别", "choice", "pump", choices=["pump", "valve"],
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置")
        add(_e(f"cur_pumptex_{shape}", name, "水景/波浪", f"{name}，波形驱动泵压。",
               "scalar", shape, ["pump", "valve"], icon, "基础", p))

    # ════════════════════════════════════════════════════════════════════
    # 电机 / 单轴扫 — 波形驱动单轴（摆喷扫动纹理）
    # ════════════════════════════════════════════════════════════════════
    msweep = [
        ("sine", "单轴正弦扫", "wave-sine"), ("tri", "单轴三角扫", "triangle"),
        ("saw_up", "单轴锯齿扫", "wave-saw-tool"), ("bounce", "单轴弹扫", "arrow-bounce"),
        ("fold", "单轴折扫", "fold"),
    ]
    for shape, name, icon in msweep:
        for axis, asuf in [("motor_x", "X"), ("motor_y", "Y")]:
            p = _scalar_params(speed=0.6, freq=1.0, lo=0, hi=255, ease=True, ch=axis)
            p[0] = ParamSpec("ch_type", "通道类别", "choice", axis, choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置")
            add(_e(f"cur_msweep_{shape}_{asuf}", f"{name}·{asuf}轴", "电机/波形",
                   f"{name}，{asuf}轴。", "scalar", shape, [axis], icon, "基础", p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / 颜色场景 — 固定调色板双色呼吸（场景化命名）
    # ════════════════════════════════════════════════════════════════════
    scenes = [
        ("日落", 0.05, 0.12), ("朝霞", 0.95, 0.08), ("月光", 0.58, 0.62),
        ("深海", 0.55, 0.66), ("森林", 0.30, 0.42), ("紫氛", 0.78, 0.85),
        ("霓虹", 0.85, 0.45), ("薄荷", 0.42, 0.50), ("玫瑰金", 0.95, 0.10),
        ("午夜蓝", 0.62, 0.70), ("火山", 0.02, 0.08), ("糖果", 0.90, 0.55),
    ]
    for name, ha, hb in scenes:
        p = _color_params(speed=0.3, freq=1.0)
        p += [ParamSpec("hue_a", "色A", "float", ha, 0.0, 1.0, 0.01,
            tooltip="控制第一种颜色的色相。0~360 度覆盖完整色环"),
              ParamSpec("hue_b", "色B", "float", hb, 0.0, 1.0, 0.01,
                  tooltip="控制第二种颜色的色相。0~360 度覆盖完整色环")]
        add(_e(f"cur_scene_{suf2(name)}", f"{name}色氛", "灯光/氛围",
               f"{name}主题双色缓变氛围。", "color", "dualtone", RGB, "color-swatch", "中级", p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 波形第三批 — 极慢/极快速度角色（铺底 vs 强节奏）
    # ════════════════════════════════════════════════════════════════════
    wave_roles3 = []  # 极慢/极快是速度参数，已合并到主波形
    for shape, zh, icon in waves:
        for suf, d, ov, tier in wave_roles3:
            p = _scalar_params(speed=ov["speed"], freq=ov["spatial_freq"], ease=True)
            add(_e(f"cur_wave3_{shape}{suf2(suf)}", f"{zh}波{suf}", "通用/波形",
                   f"{zh}波形，{d}。", "scalar", shape, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 跑灯第二批 — 速度角色
    # ════════════════════════════════════════════════════════════════════
    chase_speed = []  # 速度是运行时参数
    for variant, base, d, icon, ov, tier0 in chases:
        for ssuf, sp, tier in chase_speed:
            p = [
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", sp, 0.0, 12.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度", "float", ov.get("width", 0.12), 0.01, 1.0, 0.01,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("tail", "拖尾", "float", ov.get("tail", 0.5), 0.0, 1.0, 0.02,
                    tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
                ParamSpec("direction", "方向", "choice", "正向", choices=["正向", "反向", "中心向外", "两侧向中"],
                    tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            add(_e(f"cur_chase2_{variant}{suf2(ssuf)}", f"{base}{ssuf}", "通用/跑灯",
                   f"{d}（{ssuf[1:]}）。", "chase", variant, ALL, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 电机 / 轨迹速度角色 — 慢/快观感差异显著
    # ════════════════════════════════════════════════════════════════════
    motor_speed = [
        ("circle", "圆形轨迹", "circle-dot"), ("ellipse", "椭圆轨迹", "oval-vertical"),
        ("fig8", "八字轨迹", "infinity"), ("spiral", "螺旋轨迹", "spiral"),
        ("sweep", "弧形扫描", "wave-saw-tool"), ("rose", "玫瑰线", "flower"),
    ]
    for variant, base, icon in motor_speed:
        for ssuf, sp, tier in [("", 0.6, "基础")]:  # 速度是参数
            p = _motor_params(speed=sp, fx=5 if variant == "rose" else 1.0)
            add(_e(f"cur_ms_{variant}{suf2(ssuf)}", f"{base}{ssuf}", "电机/轨迹",
                   f"{base}（{ssuf[1:]}）。", "motor", variant, ["motor_x", "motor_y"], icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 水景 / 泵脉冲包络 — ADSR/阶梯/斜坡 驱动泵（喷涌节奏）
    # ════════════════════════════════════════════════════════════════════
    water_env = [
        ("adsr", "泵打击喷涌", "adjustments-bolt", dict(attack=0.03, decay=0.2, speed=1.8), "中级"),
        ("adsr", "泵柔和喷涌", "adjustments", dict(attack=0.3, decay=0.4, speed=0.6), "中级"),
        ("step", "泵四级水位", "stairs-up", dict(steps=4, speed=0.6), "基础"),
        ("step", "泵八级水位", "stairs-up", dict(steps=8, speed=0.6), "基础"),
        ("ramp_hold", "泵蓄势喷发", "arrow-ramp-right", dict(speed=0.6), "中级"),
        ("sh", "泵随机喷涌", "stairs", dict(speed=1.2), "中级"),
    ]
    for variant, name, icon, ov, tier in water_env:
        p = [
            ParamSpec("ch_type", "通道类别", "choice", "pump", choices=["pump", "valve"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", ov.get("speed", 1.0), 0.0, 12.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("attack", "起音", "float", ov.get("attack", 0.1), 0.0, 1.0, 0.01,
                tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
            ParamSpec("decay", "衰减", "float", ov.get("decay", 0.3), 0.0, 1.0, 0.01,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
            ParamSpec("steps", "级数", "int", ov.get("steps", 4), 2, 16, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
                tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
            ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        add(_e(f"cur_we_{variant}_{suf2(name)}", name, "水景/脉冲", f"{name}。", "env", variant, ["pump", "valve"], icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / 颜色方向 — 新色彩模式 × 方向（扩散/汇聚）
    # ════════════════════════════════════════════════════════════════════
    cdir = [("colorwave", "颜色波浪", "wave-sine"), ("ocean", "海洋波光", "ripple"),
            ("sparkles", "极光", "sparkles"), ("rainbow", "彩虹", "rainbow")]
    for variant, base, icon in cdir:
        for dsuf, spreadv, tier in []:  # direction is a runtime param
            p = _color_params(speed=0.45, freq=1.2)
            for ps in p:
                if ps.key == "spread":
                    ps.default = spreadv
            add(_e(f"cur_cdir_{variant}{suf2(dsuf)}", f"{base}{dsuf}", "灯光/颜色",
                   f"{base}沿方向{dsuf[1:]}。", "color", variant, RGB, icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 呼吸第二批 — 套 RGB 整体亮度呼吸（色彩呼吸铺底）
    # ════════════════════════════════════════════════════════════════════
    breathe2 = [
        ("breathe", "整片呼吸", "lungs", dict(speed=0.3)),
        ("heartbeat", "脉律", "heartbeat", dict(speed=1.0)),
        ("tide", "潮汐铺底", "ripple", dict(speed=0.25)),
    ]
    for variant, name, icon, ov in breathe2:
        for chsuf, ch in [("", "dimmer")]:  # ch_type is a choice param
            p = [
                ParamSpec("ch_type", "通道类别", "choice", ch, choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", ov.get("speed", 0.4), 0.0, 6.0, 0.02,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
                    tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            add(_e(f"cur_br2_{variant}{suf2(chsuf)}", f"{name}{chsuf}", "通用/渐变",
                   f"{name}（{chsuf[1:]}）。", "breathe", variant, ALL, icon, "基础", p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / RGB 跑色 — 彩色追逐（chase 内核套 R/G/B 实为亮度跑，配多色名）
    # ════════════════════════════════════════════════════════════════════
    rgb_chase = [
        ("comet", "彩色彗星", "comet"), ("scan", "彩色扫描", "scan"),
        ("wave-saw-tool", "彩色蛇行", "wave-saw-tool"), ("dot", "彩色跑点", "point"),
    ]
    for variant, base, icon in rgb_chase:
        for csuf, ch in [("", "dimmer")]:  # ch_type is a choice param
            p = [
                ParamSpec("ch_type", "通道类别", "choice", ch, choices=["R", "G", "B", "W", "dimmer"],
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 1.5, 0.0, 12.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度", "float", 0.12, 0.01, 1.0, 0.01,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("tail", "拖尾", "float", 0.5, 0.0, 1.0, 0.02,
                    tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
                ParamSpec("direction", "方向", "choice", "正向", choices=["正向", "反向", "中心向外", "两侧向中"],
                    tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            add(_e(f"cur_rgbchase_{variant}{suf2(csuf)}", f"{base}{csuf}", "灯光/跑灯",
                   f"{base}，{csuf[1:]}通道。", "chase", variant, ["R", "G", "B", "W"], icon, "中级", p))

    # ════════════════════════════════════════════════════════════════════
    # 电机 / Z 轴升降 — 第三轴动作（摆喷俯仰/升降）
    # ════════════════════════════════════════════════════════════════════
    zmotor = [
        ("sine", "Z轴正弦升降", "wave-sine"), ("tri", "Z轴三角升降", "triangle"),
        ("bounce", "Z轴弹跳", "arrow-bounce"), ("step", "Z轴阶梯升", "stairs-up"),
        ("breathe", "Z轴呼吸升降", "lungs"),
    ]
    for variant, name, icon in zmotor:
        if variant in ("sine", "tri", "bounce"):
            p = _scalar_params(speed=0.5, freq=1.0, ease=True, ch="motor_z")
            p[0] = ParamSpec("ch_type", "通道类别", "choice", "motor_z", choices=["motor_z", "motor_x", "motor_y"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置")
            add(_e(f"cur_zmotor_{variant}", name, "电机/造型", f"{name}。", "scalar", variant, ["motor_z"], icon, "基础", p))
        else:
            p = [
                ParamSpec("ch_type", "通道类别", "choice", "motor_z", choices=["motor_z", "motor_x", "motor_y"],
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", 0.6, 0.0, 8.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("steps", "级数", "int", 4, 2, 16, 1,
                    tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
                ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
                    tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            k = "env" if variant == "step" else "breathe"
            add(_e(f"cur_zmotor_{variant}", name, "电机/造型", f"{name}。", k, variant, ["motor_z"], icon, "中级", p))

    # ════════════════════════════════════════════════════════════════════
    # 水景 / 阀门第二批 — 阀门脉冲与编排
    # ════════════════════════════════════════════════════════════════════
    valves2 = [
        ("burst", "阀门短脉冲", "bolt", dict(speed=3.0, attack=0.02, decay=0.1), "基础"),
        ("burst", "阀门长开阀", "bolt", dict(speed=0.4, attack=0.1, decay=0.6), "基础"),
        ("chase", "阀门反向跑", "arrow-left-bar", dict(speed=1.0), "基础"),
        ("cross", "阀门交叉", "arrows-cross", dict(speed=1.0), "中级"),
        ("bloom", "阀门绽放", "ripple", dict(speed=0.8), "中级"),
        ("step_water", "阀门阶梯", "stairs-up", dict(speed=0.6), "基础"),
    ]
    for variant, name, icon, ov, tier in valves2:
        p = _fountain_params(ch="valve", speed=ov.get("speed", 1.0))
        for ps in p:
            if ps.key == "attack" and "attack" in ov: ps.default = ov["attack"]
            if ps.key == "decay" and "decay" in ov: ps.default = ov["decay"]
        add(_e(f"cur_valve2_{variant}_{suf2(name)}", name, "水景/阀门", f"{name}。", "fountain", variant, ["valve"], icon, tier, p))

    # ════════════════════════════════════════════════════════════════════
    # 通用 / 随机第二批 — 套不同通道的随机纹理
    # ════════════════════════════════════════════════════════════════════
    rand2 = [
        ("sparkle", "泵随机喷点", "sparkles", "pump", dict(density=0.25, speed=2.0)),
        ("twinkle", "阀门微跳", "stars", "valve", dict(density=0.3, speed=1.5)),
        ("perlin", "电机有机游移", "wave-saw-tool", "motor_x", dict(speed=0.5)),
        ("jitter", "整体抖动", "activity", "dimmer", dict(density=0.4, speed=5.0)),
    ]
    for variant, name, icon, ch, ov in rand2:
        p = [
            ParamSpec("ch_type", "通道类别", "choice", ch, choices=CH_TYPES,
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", ov.get("speed", 1.0), 0.0, 12.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("density", "密度", "float", ov.get("density", 0.3), 0.0, 1.0, 0.02,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        add(_e(f"cur_rand2_{variant}_{suf2(name)}", name, "通用/渐变", f"{name}。", "rand", variant, ALL, icon, "中级", p))

    # ════════════════════════════════════════════════════════════════════
    # 灯光 / 颜色场景第二批 — 单色相旋转主题
    # ════════════════════════════════════════════════════════════════════
    hue_scenes = [
        ("赛博紫", 0.78), ("熔金", 0.10), ("翡翠", 0.38), ("珊瑚", 0.02),
        ("天青", 0.52), ("樱粉", 0.92), ("琥珀", 0.08), ("靛蓝", 0.66),
    ]
    for name, h in hue_scenes:
        p = _color_params(speed=0.35, freq=1.5)
        add(_e(f"cur_hscene_{suf2(name)}", f"{name}流转", "灯光/颜色",
               f"{name}主题色相流转。", "color", "hue_rot", RGB, "color-swatch", "基础", p))

    # 合并 A–H 高级算法族 + 电机物理族
    F.extend(build_advanced_families())

    # 合并本次扩充：电机编组/交替覆盖扇/序列程序 + 通用交替覆盖/序列 + 常规扩充
    F.extend(build_expansion_families())

    # 合并泵阀水景重写：泵水形/泵涌动/阀门门控/阀门跑阵/组合/编程秀
    F.extend(build_water_families())

    # 合并音频反应效果（真实音频驱动，按设备类型拆分5家族）
    F.extend(build_audio_light_families())
    F.extend(build_audio_color_families())
    F.extend(build_audio_motor_families())
    F.extend(build_audio_valve_families())
    F.extend(build_audio_pump_families())

    # 去重（防 id 撞）
    seen = set(); out = []
    for f in F:
        if f["id"] in seen:
            f["id"] = f["id"] + "_" + str(len(out))
        seen.add(f["id"]); out.append(f)
    return out


def _adv_params(speed=1.0, freq=3.0, scale=1.0, density=0.5, steps=6, ch="dimmer"):
    return [
        ParamSpec("ch_type", "通道类别", "choice", ch, choices=CH_TYPES,
            tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
        ParamSpec("speed", "速度", "float", speed, 0.0, 8.0, 0.05,
            tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ParamSpec("spatial_freq", "空间频率", "float", freq, 0.2, 16.0, 0.1,
            tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集"),
        ParamSpec("scale", "尺度", "float", scale, 0.1, 4.0, 0.05,
            tooltip="控制缩放比例。值越大视觉元素放大越多"),
        ParamSpec("density", "密度", "float", density, 0.0, 1.0, 0.02,
            tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
        ParamSpec("steps", "粒数/级数", "int", steps, 2, 24, 1,
            tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
        ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
            tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
        ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
            tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
        ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
            tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
    ]


# 高级算法效果家族（A–H），全部单通道 ch_type 通用，多变体。
_ADV_ROLES = [
    ("", "标准速率", dict(speed=1.0, spatial_freq=3.0), "中级"),
]

_ADV_FAMILY = [
    # variant, 基名, 分类, 说明, 图标
    ("turing", "反应扩散斑纹", "通用/高级", "自组织生长的有机斑纹", "atom"),
    ("lzwave", "弹簧链波传", "通用/高级", "真实波动沿设备线传播反射", "wave-sine"),
    ("particle", "粒子场", "通用/高级", "带重力寿命的粒子密度采样", "sparkles"),
    ("firewall", "火焰扩散", "通用/高级", "热上升加湍流的火舌蔓延", "flame"),
    ("raindrops", "落水涟漪", "通用/高级", "多落点扩散环互相干涉", "ripple"),
    ("avalanche", "沙堆雪崩", "通用/高级", "缓积累后突然崩塌级联", "stairs-down"),
    ("capillary", "毛细爬升", "通用/高级", "液面缓爬再回落的水感", "droplet"),
    ("resonant", "共振水柱", "通用/高级", "周期力驱动的共振脉动", "wave-sine"),
    ("geyser", "间歇泉律动", "通用/高级", "压力积累喷发冷却完整循环", "arrow-big-up"),
    ("chaos", "混沌振子", "通用/高级", "确定却永不重复的混沌演化", "vector-spline"),
    ("ca110", "元胞自动机·涌现", "通用/高级", "简单规则涌现复杂图案(rule110)", "binary"),
    ("ca90", "元胞自动机·分形", "通用/高级", "谢尔宾斯基分形纹(rule90)", "binary"),
    ("ca30", "元胞自动机·混沌", "通用/高级", "混沌随机纹(rule30)", "binary"),
    ("ca184", "交通流波", "通用/高级", "走停传播的车流波(rule184)", "binary"),
    ("fbm", "分形噪声场", "通用/高级", "多倍频噪声叠加的自然质感", "wave-saw-tool"),
    ("moire", "莫尔拍频", "通用/高级", "两相近频率的视觉拍", "grid-pattern"),
    ("harmonic", "谐波合成", "通用/高级", "多泛音叠加可调音色", "wave-sine"),
    ("phyllotaxis", "向日葵相位", "通用/高级", "黄金角最优相位分布", "flower"),
    ("fibonacci", "斐波那契律动", "通用/高级", "数论驱动的不规则律动", "math-symbols"),
    ("dpend", "双摆混沌", "通用/高级", "经典混沌系统投影", "vector-spline"),
    ("firefly", "萤火虫同步", "通用/高级", "各自闪烁逐渐锁相同步", "bulb"),
    ("swarm", "群集密度", "通用/高级", "群体涌动行为投影", "users-group"),
    ("forestfire", "野火蔓延", "通用/高级", "点燃蔓延烧尽再生", "flame"),
    ("pheromone", "蚁群轨迹", "通用/高级", "路径自增强的信息素痕", "route"),
    ("startle", "惊飞级联", "通用/高级", "一点触发波及全场连锁", "arrows-shuffle"),
    ("voronoi", "泰森晶格", "通用/高级", "漂移种子的距离场细胞", "polygon"),
    ("vortex", "涡旋场", "通用/高级", "旋转流中心汇聚", "rotate-clockwise"),
    ("gravity", "引力场", "通用/高级", "设备被拉向漂移引力点", "atom"),
    ("magnet", "磁极吸斥", "通用/高级", "吸引排斥双极场", "magnet"),
    ("ripplefield", "辐射涟漪场", "通用/高级", "中心同心环推进", "ripple"),
    ("interference", "双源干涉", "通用/高级", "两波源干涉条纹", "wave-sine"),
    ("standing", "驻波", "通用/高级", "节点腹点固定的驻波", "wave-sine"),
    ("shepard", "谢泼德楼梯", "通用/高级", "亮度永恒上升的错觉", "stairs-up"),
    ("physio", "生理节律", "通用/高级", "呼吸心跳叹息有机组合", "heartbeat"),
    ("drift", "错觉漂移", "通用/高级", "静态却仿佛在动", "ripple"),
    ("tidemodel", "天文潮汐", "通用/高级", "多正弦叠加的真实潮汐", "wave-sine"),
]

_MOTOR_PHYS = [
    ("damped", "阻尼振荡回中", "摆动衰减归位的真实物理", "wave-sine"),
    ("overshoot", "弹性过冲", "到位过冲再回弹", "arrow-bounce"),
    ("inertia", "惯性扫掠", "带惯性的加减速扫动", "wave-saw-tool"),
    ("resonant", "共振摆", "周期力驱动的共振摆幅", "wave-sine"),
]


def build_advanced_families():
    """A–H 高级算法族 + 电机物理族，全部多变体。供 build_families 合并。"""
    F = []
    add = F.append
    ALL = CH_TYPES

    # A–H 高级算法：每内核 × 4 速度角色（观感不同）
    for variant, base, cat, desc, icon in _ADV_FAMILY:
        for suf, d, ov, tier in _ADV_ROLES:
            p = _adv_params(speed=ov["speed"], freq=ov["spatial_freq"])
            add(_e(f"cur_adv_{variant}{suf2(suf)}", f"{base}{suf}", cat,
                   f"{desc}，{d}。", "adv", variant, ALL, icon, tier, p))

    # 电机单轴物理：每内核 × X/Y 轴
    for variant, base, desc, icon in _MOTOR_PHYS:
        for axis, asuf in [("motor_x", "X"), ("motor_y", "Y")]:
            p = _adv_params(speed=0.6, ch=axis)
            p[0] = ParamSpec("ch_type", "通道类别", "choice", axis, choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置")
            kern = "adv"
            add(_e(f"cur_mphys_{variant}_{asuf}", f"{base}·{asuf}轴", "电机/造型",
                   f"{desc}，{asuf}轴。", kern, variant, [axis], icon, "中级", p))

    return F


# ════════════════════════════════════════════════════════════════════════════
#  本次扩充：电机编组(formation) / 交替覆盖扇 / 序列程序(formseq)
#            + 通用交替覆盖(array) / 通用序列(seq) + 通用常规扩充
#  重心：电机编组与序列、把编组思想下放到任意通道。只加不减。
# ════════════════════════════════════════════════════════════════════════════
MXY = ["motor_x", "motor_y"]

# 编组造型原型：(formation kind, 中文基名, 一句说明, 图标语义名)
_FORMATION_PROTOS = [
    ("seagull", "海鸥振翅", "V字鸥形两翼上下扑动", "bird"),
    ("vee", "雁阵V飞", "队列排成V字整体起伏", "bird"),
    ("butterfly", "蝶舞开合", "中心定·两翼对称扑动", "butterfly"),
    ("dragonfly", "蜻蜓振翅", "四翼小幅高频扑动", "dragonfly"),
    ("peacock", "孔雀开屏", "扇面渐次张开成屏", "peacock"),
    ("peacock_close", "孔雀收屏", "从全开缓缓合拢", "peacock"),
    ("arch", "拱桥起伏", "tilt抛物线整体呼吸", "bridge"),
    ("wheat", "麦浪", "tilt行进正弦如风吹麦", "wheat"),
    ("dualwave", "双波交织", "两列反向行进波叠加", "wave-sine"),
    ("heart", "心形绽放", "阵列连成跳动心形", "heart"),
    ("crown", "王冠环绕", "环形角度齐起伏成冠", "crown2"),
    ("fountain", "喷涌发散", "中心向外喷射张角", "fan"),
    ("fan_open", "扇形开合", "整把扇原地开合", "fan-fold"),
    ("phyllo", "花序螺旋", "黄金角分布旋转生长", "spiral2"),
    ("pendarray", "钟摆阵", "相位失谐成物理钟摆波", "pendulum"),
    ("seesaw", "跷跷板", "奇偶设备反向起落", "seesaw"),
    ("mirrordance", "镜花对舞", "左右镜像对称舞动", "mirror"),
    ("funnel", "漏斗汇聚", "向中心收拢再张开", "funnel"),
    ("zipper", "拉链开合", "从中心向两端依次开", "zipper"),
    ("dna", "DNA双螺旋", "两股相位相反旋绕", "dna"),
    ("ripple_out", "涟漪外扩", "tilt同心环向外扩散", "ripple2"),
    ("dragon", "游龙摆尾", "S形行波幅度沿线增大", "dragon"),
    ("horn", "号角张合", "喇叭口对称开合", "horn"),
    ("mountains", "群山起伏", "错相高低峰峦连绵", "mountain"),
    ("checker", "棋盘错动", "奇偶分块反向平移", "checker"),
    ("windmill", "风车旋臂", "绕中心整体旋转", "windmill"),
    ("fishtail", "鱼尾S摆", "尾端摆幅渐大的S摆", "fish"),
    ("satellite", "卫星绕心", "中心定·周围绕圈", "satellite"),
    ("weave", "编织交叉", "相邻设备交叉穿插", "weave"),
    ("domino", "倾倒多米诺", "依次倾倒再立起", "domino"),
    ("scissors", "剪刀对开", "上下两组反向开合", "scissors"),
    ("cascade", "此起彼伏", "组间相位差行进起伏", "cascade"),
    ("school", "鱼群涌动", "群体同向缓漂带个体偏移", "school"),
    ("turn", "列阵转向", "整列依次转向同角", "compass"),
    ("tide", "潮汐摆", "缓慢左右摇摆带位差", "ripple2"),
    ("galaxy", "星河旋臂", "对数螺旋旋臂旋转", "galaxy"),
    ("spread", "聚拢发散", "径向呼吸聚散", "expand"),
    ("bow", "拱形波", "弓形起伏整体平移", "bow"),
    ("flutter", "全场振翅", "全体高频小幅扑动", "flutter"),
    ("spiral_in", "螺旋收束", "边旋边向心收拢", "spiral2"),
    ("swing", "整列同摆", "单摆同步左右摆", "swing"),
    ("vortex_form", "漩涡阵", "越靠外旋转越快", "tornado"),
    # ── 本次扩充：50 个独立电机水型（22 编组 kind + 3 序列，2026-08-12）──
    # 题材广泛：几何/星象/自然/人文/流体/抽象，动物仅 2。全部显式消费
    # groups 分组语义（gi/gk/gpos），组间状态不同、组内各异。
    ("whirlpool", "漩涡吞卷", "旋臂吞卷，逐层收拢，内旋下沉", "tornado"),
    ("snowfall", "飞雪漫舞", "雪片漫天，层叠错落，旋落无声", "snowflake"),
    ("taiji", "太极旋运", "阴阳互抱，双弧逆旋，吐纳呼吸", "yin-yang"),
    ("windchime", "风铃摇响", "铃穗错鸣，风过脉动，此起彼伏", "bell"),
    ("dandelion", "蒲公英散", "金瓣散逸，环环错时，轻盈远扬", "flower"),
    ("star_trails", "星轨旋舞", "同心环轨，奇偶逆旋，星辉微颤", "star"),
    ("meteor_rain", "流星逐夜", "流星曳尾，错时划过，次第疾驰", "meteor"),
    ("moon_phases", "月相盈缺", "盈亏明暗，弧光流转，月照中天", "moon"),
    ("hexagon_breath", "蜂巢六边", "六角蜂巢，邻边对息，边壁柔弯", "hexagon"),
    ("constellation", "星座连线", "星图连线，碎芒明灭，轮廓流转", "stars"),
    ("waterfall", "飞瀑倾泻", "帘水奔落，珠碎千层，归而复始", "droplet"),
    ("aurora_dance", "极光流转", "幕帘对舞，光华漫卷，峰影徐移", "wind"),
    ("tide_wash", "潮汐洄澜", "洪波涌起，快进慢退，弧锋争岸", "ripple-up"),
    ("silk_ribbon", "绸带卷舒", "绸带蜿蜒，段段卷舒，细波相随", "bow"),
    ("bronze_bells", "编钟错鸣", "钟行错击，余音袅袅，声浪迭起", "bell"),
    ("water_wheel", "水车轮转", "巨轮徐转，斗悬轮缘，起落回环", "windmill"),
    ("kite_line", "纸鸢牵线", "纸鸢升空，牵线缓漂，错落翱翔", "triangle"),
    ("fireworks_ring", "烟花绽放", "连珠迸发，环环绽放，垂落如雨", "flare"),
    ("glissando", "滑音游走", "音符滑阶，折返游走，流光闪烁", "music"),
    ("jellyfish", "水母浮沉", "伞张伞收，触手轻摆，浮沉悠悠", "droplets"),
    ("eagle", "苍鹰盘旋", "翼展翻飞，盘旋俯瞰，振翅凌空", "feather"),
    ("compass_rose", "罗盘八向", "象限对旋，正交吐纳，叶形开合", "compass"),
]

# 编组速度/轴角色（真观感不同）
_FORM_ROLES = [
    ("", "标准速率", dict(speed=0.5, axis="双轴XY"), "基础"),
]

# 交替覆盖扇原型
_FAN_PROTOS = [
    ("fan_traverse", "行进扇", "扇心行进、新扇心落旧扇缘成重叠覆盖", "fan"),
    ("fan_breathe", "对称开合扇", "整把扇原地呼吸开合", "fan-fold"),
    ("fan_wave", "波动扇", "各扇依次开成行进波", "fan"),
    ("fan_cross", "交叉扇", "相邻扇互叉成X", "fan-fold"),
    ("fan_sweep", "全开扫掠", "全展开同向扫掠", "fan"),
    ("fan_relay", "接力扇", "一扇开一扇合相交接", "fan-fold"),
    ("fan_bidir", "双向覆盖扇", "奇偶扇反向行进覆盖", "fan"),
    ("fan_drift", "扇心漂移", "扇心缓慢左右漂移", "fan-fold"),
    ("fan_step", "阶梯开扇", "离散台阶逐级开扇", "fan"),
    ("fan_ripple", "涟漪扇", "从中心扇向外依次开", "fan-fold"),
    ("fan_mirror", "镜像扇", "左右镜像对称开扇", "fan"),
    ("fan_converge", "收束扇", "从全开向中收束", "fan-fold"),
]

_FAN_ROLES = [
    ("", "标准覆盖", dict(speed=0.5, groups=4), "中级"),
]

# 电机序列程序：(formseq kind, 中文名, 说明, 图标)
_FORMSEQ_PROTOS = [
    ("birds_dawn", "群鸟晨飞", "雁阵→振翅→散→重组", "bird"),
    ("butterfly_love", "蝶恋花", "蝶舞→花序→开屏", "butterfly"),
    ("tide_cycle", "潮起潮落", "潮汐→麦浪→拱桥→漏斗", "ripple2"),
    ("parade", "列队点兵", "转向→棋盘→风车→同摆", "march"),
    ("ceremony", "开场仪典", "漏斗→喷涌→王冠→开屏", "stage"),
    ("twin_dragons", "双龙戏珠", "游龙→螺旋→编织→鱼尾", "dragon"),
    ("shanhaijing", "山海经", "群山→潮汐→涟漪→星河", "mountain"),
    ("ballet", "机械芭蕾", "同摆→剪刀→对舞→聚散", "stage"),
    ("peacock_tour", "孔雀巡礼", "开屏→扇开→王冠→收屏", "peacock"),
    ("storm", "风暴来袭", "振翅→漩涡→鱼群→起伏", "tornado"),
    ("daybreak", "破晓", "拱桥→喷涌→发散→花序", "celestial"),
    ("galaxy_flow", "星河流转", "星河→螺旋→风车→花序", "galaxy"),
    ("review", "阅兵分列", "雁阵→转向→棋盘→同摆", "march"),
    ("hundred_birds", "百鸟朝凤", "鱼群→雁阵→振翅→王冠", "bird"),
    ("tornado", "龙卷", "漩涡→螺旋→漏斗→星河", "tornado"),
    ("ripple_sym", "涟漪交响", "涟漪→王冠→双波→潮汐", "ripple2"),
    ("fan_show", "扇舞", "开合扇→波动扇→行进扇→交叉扇", "fan"),
    ("wings", "振翅组曲", "海鸥→蝶舞→蜻蜓→振翅", "wings"),
    ("bloom_seq", "绽放", "漏斗→发散→心形→花序", "heart"),
    ("ocean", "海之歌", "潮汐→麦浪→双波→喷涌", "ripple2"),
    ("march_wave", "行进波", "多米诺→此起彼伏→拉链→群山", "march"),
    ("celestial", "天体", "星河→花序→王冠→涟漪", "celestial"),
    ("courtship", "求偶之舞", "开屏→蝶舞→蜻蜓→心形", "peacock"),
    ("machine", "机械律动", "棋盘→风车→剪刀→转向", "compass"),
    ("serpent", "群蛇", "游龙→鱼尾→螺旋→编织", "dragon"),
    ("phoenix", "凤舞", "喷涌→开屏→王冠→振翅", "phoenix"),
    ("carnival", "嘉年华", "波动扇→风车→鱼群→发散", "carnival"),
    ("meditation", "静心", "潮汐→同摆→拱桥→聚散", "meditation"),
    ("victory", "凯旋", "雁阵→王冠→喷涌→开屏", "victory"),
    ("kaleido", "万花筒", "对舞→花序→星河→风车", "kaleidoscope"),
    # ── 本次扩充：3 个序列（2026-08-12，段全部来自新编组 kind）──
    ("star_odyssey", "星夜漫游", "星轨→星座→流星→星河", "stars"),
    ("four_seasons", "四季流转", "春絮→夏水→秋风→冬雪", "flower"),
    ("nine_heavens", "九天揽月", "鹰翔→流星→月相→烟花", "moon"),
]

# 通用交替覆盖：(array kind, 中文名, 说明, 图标)
_ARRAY_PROTOS = [
    ("cover", "组块覆盖波", "分组三角行进、重叠覆盖", "cover"),
    ("cascade", "此起彼伏", "组间相位差起伏", "cascade"),
    ("relay", "接力波", "一组亮完下一组接力", "relay"),
    ("overlap", "重叠行进波", "多正弦相位叠加行进", "overlap"),
    ("mirror", "镜像覆盖", "左右对称向中推进", "mirror"),
    ("zipperfill", "拉链填充", "从中心向两端拉满", "zipper"),
    ("parity", "奇偶交替", "奇偶两组反相闪烁", "alternate"),
    ("breathe_grp", "组块呼吸", "各组整体呼吸错相", "cover"),
    ("stepcover", "阶梯覆盖", "离散组依次满格", "stack"),
    ("ripplegrp", "涟漪组波", "中心组起波向外扩", "ripple2"),
    ("dualflow", "双向对涌", "两端向中相遇", "overlap"),
    ("peakdrift", "波峰漂移", "单峰沿阵列缓移", "peak"),
    ("rotate", "扇区轮播", "依次点亮各扇区", "rotate2"),
    ("wavefront", "波峰行进", "波峰单向扫过阵列", "wave-sine"),
]

_ARRAY_ROLES = [
    ("·四组", dict(speed=1.0, groups=4), "基础"),
    ("·八组", dict(speed=1.0, groups=8), "中级"),
    ("·疾覆", dict(speed=2.4, groups=4), "中级"),
]

# 通用序列程序：(seq kind, 中文名, 说明, 图标)
_SEQ_PROTOS = [
    ("intro_arc", "起承转合", "呼吸→正弦→彗星→火花", "sequence"),
    ("climax", "渐入高潮", "正弦→彗星→火花→方波", "program"),
    ("calm_storm", "静极生动", "呼吸→三角→抖动", "sequence"),
    ("color_journey", "色彩之旅", "彩虹→火焰→极光", "rainbow"),
    ("static_dyn", "静动静", "呼吸→跑点→呼吸", "sequence"),
    ("build_drop", "蓄势爆发", "渐亮→火花→方波→呼吸", "program"),
    ("pulse_run", "脉冲奔流", "脉冲→彗星→方波", "sequence"),
    ("wave_suite", "波形组曲", "正弦→三角→折叠→双峰", "wave-sine"),
    ("night_day", "昼夜", "呼吸→渐亮→火花→渐暗", "sequence"),
    ("heartbeat_run", "心动奔流", "心跳→彗星→心跳", "ecg"),
    ("spark_flow", "火花流动", "火花→正弦→跑点", "sequence"),
    ("rise_fall", "升落", "渐亮→正弦→渐暗", "program"),
    ("groove", "律动", "方波→脉冲→彗星", "sequence"),
    ("ambient_drift", "氛围漂移", "呼吸→柏林→正弦", "film"),
    ("festival", "节庆", "彗星→火花→方波→跑点", "carnival"),
    ("ocean_seq", "海序", "正弦→潮汐→双峰", "ripple2"),
    ("fire_seq", "火序", "抖动→指数→火花", "phoenix"),
    ("march_seq", "进行曲", "方波→跑点→脉冲", "march"),
    ("dream_seq", "梦境", "呼吸→彩虹→柏林", "film"),
    ("finale", "终曲", "正弦→彗星→火花→方波→呼吸", "stage"),
]


def build_expansion_families():
    F = []
    add = F.append
    ALL = CH_TYPES

    # ─── 电机 / 编组造型 ───
    for kind, base, desc, icon in _FORMATION_PROTOS:
        for suf, rd, ov, tier in _FORM_ROLES:
            p = _formation_params(speed=ov["speed"], axis=ov["axis"])
            add(_e(f"cur_form_{kind}{suf2(suf)}", f"{base}{suf}", "电机/编组",
                   f"{desc}，{rd}。", "formation", kind, MXY, icon, tier, p))
        # 单轴Y 造型变体（俯仰编队，观感不同）
        p2 = _formation_params(speed=0.5, axis="单轴Y")
        add(_e(f"cur_form_{kind}_y", f"{base}·俯仰", "电机/编组",
               f"{desc}，仅俯仰单轴。", "formation", kind, ["motor_y"], icon, "中级", p2))

    # ─── 电机 / 交替覆盖扇 ───
    for kind, base, desc, icon in _FAN_PROTOS:
        for suf, rd, ov, tier in _FAN_ROLES:
            p = _formation_params(speed=ov["speed"], groups=ov["groups"])
            add(_e(f"cur_fan_{kind}{suf2(suf)}", f"{base}{suf}", "电机/扇",
                   f"{desc}，{rd}。", "formation", kind, MXY, icon, tier, p))

    # ─── 电机 / 序列程序 ───
    for kind, base, desc, icon in _FORMSEQ_PROTOS:
        p = _formseq_params()
        add(_e(f"cur_fseq_{kind}", base, "电机/序列", f"{desc}（流畅串接）。",
               "formseq", kind, MXY, icon, "高级", p))
        # 慢演一版（停留更久，仪式感）
        p2 = _formseq_params(speed=0.15, hold=0.75)
        add(_e(f"cur_fseq_{kind}_slow", f"{base}·缓演", "电机/序列",
               f"{desc}，停留更久。", "formseq", kind, MXY, icon, "高级", p2))

    # ─── 通用 / 交替覆盖 ───
    for kind, base, desc, icon in _ARRAY_PROTOS:
        for suf, ov, tier in _ARRAY_ROLES:
            p = _arraycov_params(speed=ov["speed"], groups=ov["groups"])
            add(_e(f"cur_arr_{kind}{suf2(suf)}", f"{base}{suf}", "通用/交替覆盖",
                   f"{desc}，可套任意通道。", "array", kind, ALL, icon, tier, p))

    # ─── 通用 / 序列程序 ───
    for kind, base, desc, icon in _SEQ_PROTOS:
        p = _seqprog_params()
        add(_e(f"cur_seq_{kind}", base, "通用/序列", f"{desc}（look流畅切换）。",
               "seq", kind, ALL, icon, "高级", p))

    # ─── 通用 / 波形 — 新基础波形 ───
    new_waves = [
        ("trapz", "梯形波", "软边方波，可调平台宽", "wave-square", 0.5),
        ("gauss", "高斯钟脉冲", "每周期一个钟形包", "wave-sine", 0.5),
        ("rectsin", "整流驼峰", "全波整流的双倍密驼峰", "wave-sine", 0.5),
        ("skewtri", "偏锋三角", "峰位前移，锯齿↔三角过渡", "triangle", 0.25),
        ("dpulse", "双脉冲", "一拍内两击", "grip-horizontal", 0.5),
        ("flattop", "平顶正弦", "峰被削平的正弦", "wave-square", 0.5),
        ("stairsin", "阶梯正弦", "量化成台阶的正弦", "stairs-up", 0.6),
    ]
    nw_roles = [
        ("·缓流", "缓慢流动铺底", dict(speed=0.4, spatial_freq=1.0), "基础"),
        ("·疾走", "快速推进", dict(speed=2.2, spatial_freq=1.5), "基础"),
        ("·密列", "高空间频率密集", dict(speed=1.0, spatial_freq=4.0), "中级"),
    ]
    for shape, zh, desc, icon, dft in new_waves:
        for suf, rd, ov, tier in nw_roles:
            p = _scalar_params(speed=ov["speed"], freq=ov["spatial_freq"], ease=True)
            p.insert(3, ParamSpec("duty", "占空/形参", "float", dft, 0.01, 1.0, 0.01,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"))
            add(_e(f"cur_nw_{shape}{suf2(suf)}", f"{zh}{suf}", "通用/波形",
                   f"{desc}，{rd}。", "scalar", shape, ALL, icon, tier, p))

    # ─── 通用 / 跑灯 — 新跑动 ───
    new_chase = [
        ("marquee", "跑马灯", "每N格亮整体平移", "marquee", dict(width=0.2)),
        ("kitt", "来回扫", "带拖尾的往返扫(KITT)", "scanner", dict(width=0.12)),
        ("build", "逐格堆积", "依次点亮累积填满", "stack", dict(width=0.1)),
        ("dissolve", "逐格消解", "依次熄灭消解", "eraser", dict(width=0.1)),
        ("jumpseq", "跳序追逐", "确定性乱序点亮", "dice", dict(width=0.1)),
        ("collide", "双彗对撞", "两头相向中点相遇", "collide", dict(width=0.12, tail=0.6)),
        ("multihead", "多头追逐", "等距多个头同跑", "multihead", dict(width=0.12, tail=0.5)),
        ("levelbar", "电平条", "到水位全亮随波起伏", "levelbar", dict(width=0.1)),
        ("mirror", "镜像追逐", "左右对称同跑", "mirror", dict(width=0.12, tail=0.5)),
    ]
    nc_roles = [("·徐行", 0.6, "基础"), ("·疾驰", 2.6, "中级")]
    for variant, base, desc, icon, ov in new_chase:
        for ssuf, sp, tier in nc_roles:
            p = [
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", sp, 0.0, 12.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度", "float", ov.get("width", 0.12), 0.01, 1.0, 0.01,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("tail", "拖尾", "float", ov.get("tail", 0.5), 0.0, 1.0, 0.02,
                    tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
                ParamSpec("direction", "方向", "choice", "正向",
                          choices=["正向", "反向", "中心向外", "两侧向中"],
                    tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            if variant == "multihead":
                p.insert(4, ParamSpec("heads", "头数", "int", 3, 2, 8, 1,
                    tooltip="控制多头/喷头的数量。值越大并行的喷头越多"))
            add(_e(f"cur_nch_{variant}{suf2(ssuf)}", f"{base}{ssuf}", "通用/跑灯",
                   f"{desc}（{ssuf[1:]}）。", "chase", variant, ALL, icon, tier, p))

    # ─── 通用 / 渐变·随机 — 新包络与随机 ───
    new_env = [
        ("pluck", "拨弦", "瞬起指数衰落", "pluck", dict(decay=0.2, speed=1.5)),
        ("swell", "渐强保持", "缓升到顶保持", "swell", dict(attack=0.4, speed=0.6)),
        ("multistrike", "连击", "一周期多次衰减击", "strike", dict(decay=0.15, steps=4, speed=1.0)),
    ]
    for variant, name, desc, icon, ov in new_env:
        for ssuf, sp, tier in [("·缓", ov.get("speed", 1.0) * 0.6, "基础"), ("·急", ov.get("speed", 1.0) * 1.8, "中级")]:
            p = [
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", sp, 0.0, 16.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("attack", "起音", "float", ov.get("attack", 0.1), 0.0, 1.0, 0.01,
                    tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
                ParamSpec("decay", "衰减", "float", ov.get("decay", 0.3), 0.0, 1.0, 0.01,
                    tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
                ParamSpec("steps", "级数", "int", ov.get("steps", 4), 2, 16, 1,
                    tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
                ParamSpec("spread", "相位展开", "float", 0.0, 0.0, 2.0, 0.05,
                    tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
                ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                    tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            add(_e(f"cur_nenv_{variant}{suf2(ssuf)}", f"{name}{ssuf}", "通用/渐变",
                   f"{desc}（{ssuf[1:]}）。", "env", variant, ALL, icon, tier, p))

    new_rand = [
        ("popcorn", "爆米花", "随机点燃后各自淡出", "popcorn", dict(density=0.3, speed=1.5)),
        ("glitch", "故障跳变", "随机阶跃故障感", "glitch", dict(density=0.4, speed=2.0)),
        ("lightning", "闪电", "偶发强闪带余辉", "lightning", dict(density=0.3, speed=1.0)),
        ("ripple", "涟漪触发", "随机落点扩散环干涉", "ripple2", dict(density=0.3, speed=0.8)),
    ]
    for variant, name, desc, icon, ov in new_rand:
        for ssuf, dn, tier in [("·稀", ov.get("density", 0.3) * 0.6, "基础"), ("·密", min(0.9, ov.get("density", 0.3) * 1.6), "中级")]:
            p = [
                ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", ov.get("speed", 1.0), 0.0, 12.0, 0.05,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("density", "密度", "float", dn, 0.0, 1.0, 0.02,
                    tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
                ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                    tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ]
            add(_e(f"cur_nrand_{variant}{suf2(ssuf)}", f"{name}{ssuf}", "通用/渐变",
                   f"{desc}（{ssuf[1:]}）。", "rand", variant, ALL, icon, tier, p))

    # ─── 通用 / 高级 — 深挖创新算法 ───
    new_adv = [
        ("pendwave", "钟摆波", "相位失谐摆阵的聚散行波", "pendwave"),
        ("lorenz", "洛伦兹吸引子", "蝴蝶混沌轨迹演化", "lorenz"),
        ("soliton", "孤波对穿", "两孤立子穿越互不干扰", "soliton"),
        ("wavepacket", "波包色散", "高斯包络载波色散漂移", "wavepacket"),
        ("metaball", "融球", "等值面阈值合并的水银融并", "metaball"),
        ("diffusion", "热扩散", "墨滴扩散变宽再复位", "diffusion"),
        ("warp", "扭曲噪声", "域扭曲的漩涡噪声质感", "warp"),
        ("doppler", "多普勒", "移动源压缩拉伸波前", "doppler"),
        ("fracband", "分形带", "1D逃逸时间分形条纹", "fractal"),
        ("langton", "朗顿蚂蚁", "简单规则涌现复杂轨迹", "ant"),
        ("vortexstreet", "涡街", "交替脱落的卡门涡街", "vortex2"),
        ("lightningfork", "闪电分叉", "主干带随机分支瞬亮", "lightning"),
        ("string", "弦振动模态", "多驻波模态叠加", "string"),
        ("ecg", "心电波", "PQRST波群沿线行进", "ecg"),
        ("contour", "等高线行进", "噪声场的等值线带", "contour"),
    ]
    for variant, base, desc, icon in new_adv:
        for suf, rd, ov, tier in _ADV_ROLES:
            p = _adv_params(speed=ov["speed"], freq=ov["spatial_freq"])
            add(_e(f"cur_nadv_{variant}{suf2(suf)}", f"{base}{suf}", "通用/高级",
                   f"{desc}，{rd}。", "adv", variant, ALL, icon, tier, p))

    return F


# ════════════════════════════════════════════════════════════════════════════
#  泵阀水景重写（仿 formation 范式，pump 高度场 / valve 门控场 彻底分开）
#  数量管够：每原型 × 真观感角色(速度/方向/组数/占空/宽窄/开合)。确定性可复现。
# ════════════════════════════════════════════════════════════════════════════
PV_PUMP = ["pump"]
PV_VALVE = ["valve"]
PV_BOTH = ["pump", "valve"]


def build_water_families():
    F = []
    add = F.append

    # ─── 水景/泵 — 高度剪影场 ───
    pumpforms = [
        # variant, 中文基名, 说明, 图标, 默认参数dict, 角色集
        ("wavewall", "波浪水墙", "高度沿阵列行进的正弦水幕", "water-wall",
         dict(speed=1.0, freq=1.0), "freqdir"),
        ("arch_static", "水拱·静拱", "抛物高度拼成拱门整体呼吸", "arch",
         dict(speed=0.6, span=1.0), "speed"),
        ("arch_move", "水拱·行拱", "拱顶沿阵列左右行走", "arch",
         dict(speed=0.5, span=1.0), "speed"),
        ("crown", "皇冠涌", "环列齐起伏带微相位差成冠", "crown-water",
         dict(speed=0.8, freq=1.0), "speeddir"),
        ("willow", "垂柳", "中心高边缘垂落带轻摆", "willow-water",
         dict(speed=0.6, bend=2.0), "speed"),
        ("peacock_open", "雀尾开屏", "高度自中向缘渐增成扇尾开", "peacock-water",
         dict(speed=0.5), "speed"),
        ("peacock_close", "雀尾收屏", "从全开缓缓合拢", "peacock-water",
         dict(speed=0.5), "speed"),
        ("peaks", "群峰", "固定错落峰高轻摆", "peaks-water",
         dict(speed=0.6, groups=4), "groups"),
        ("swell", "涌浪", "整阵同步压力涌起落", "swell",
         dict(speed=0.5), "speed"),
        ("tide", "潮汐水位", "主涌叠次涟漪的水位", "tide-water",
         dict(speed=0.3), "speed"),
        ("runjet", "跑泉", "高斯高度包沿阵列奔跑", "runjet",
         dict(speed=1.0, width=0.12), "widthdir"),
        ("runjet_multi", "跑泉·多头", "等距多个高度头同跑", "runjet",
         dict(speed=1.0, width=0.1), "heads"),
        ("cascade", "此起彼伏", "分组高度错相起伏", "swell",
         dict(speed=1.0, groups=4), "groups"),
        ("opposing", "对涌", "奇偶喷头高低交替反相", "alternate",
         dict(speed=0.8), "speed"),
        ("spiral", "螺旋水阵", "多臂行进高度环形阵", "spiral-water",
         dict(speed=0.8, groups=4), "groups"),
        ("bloom", "绽放", "高度环自中心向外扩散", "bloom-water",
         dict(speed=0.6), "speeddir"),
        ("stair_water", "阶梯水位", "量化高度成台地行进", "stair-water",
         dict(speed=0.6, steps=4), "steps"),
    ]
    # 涌动时间型
    pump_surge = [
        ("supershoot_salvo", "超级水炮·齐射", "全体慢蓄高喷急落", "watercannon",
         dict(speed=0.5), "speed"),
        ("supershoot_seq", "超级水炮·顺射", "顺序单点慢蓄高喷", "watercannon",
         dict(speed=0.5), "speed"),
        ("pulse_surge", "脉涌", "双拍压力脉冲水在跳", "pulse-water",
         dict(speed=1.0), "speed"),
        ("geyser", "间歇泉律动", "蓄压喷发冷却完整循环", "geyser",
         dict(speed=0.5), "speed"),
    ]

    SPEED_ROLES = [("", 1.0, "基础")]  # speed is runtime param
    DIR_ROLES = [("", "正向", "基础")]  # direction is runtime param
    FREQ_ROLES = [("", 1.0, "基础")]  # freq is runtime param
    GROUP_ROLES = [("", 4, "基础")]  # groups is runtime param
    WIDTH_ROLES = [("", 0.16, "基础")]  # width is runtime param
    STEP_ROLES = [("", 4, "基础")]  # steps is runtime param
    HEAD_ROLES = [("", 2, "基础")]  # heads is runtime param

    def _pump_one(variant, name, desc, icon, base, suf, tier, ov):
        extra = []
        if variant in ("arch_static", "arch_move"):
            extra = [ParamSpec("span", "拱宽", "float", base.get("span", 1.0), 0.3, 2.0, 0.05,
                tooltip="控制效果跨越的范围幅度。值越大影响区域越广")]
        if variant == "willow":
            extra = [ParamSpec("bend", "垂度", "float", base.get("bend", 2.0), 0.5, 4.0, 0.1,
                tooltip="控制弯曲程度。值越大曲线或轨迹的弯曲越明显")]
        if variant == "runjet_multi":
            extra = [ParamSpec("heads", "头数", "int", base.get("heads", 3), 2, 8, 1,
                tooltip="控制多头/喷头的数量。值越大并行的喷头越多")]
        if variant == "stair_water":
            extra = [ParamSpec("steps", "级数", "int", base.get("steps", 4), 2, 16, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细")]
        p = _pumpform_params(speed=ov.get("speed", base.get("speed", 1.0)),
                             freq=ov.get("freq", base.get("freq", 1.0)),
                             width=ov.get("width", base.get("width", 0.18)),
                             groups=ov.get("groups", base.get("groups", 3)),
                             direction=ov.get("direction", "正向"), extra=extra)
        add(_e(f"cur_pf_{variant}{suf2(suf)}", f"{name}{suf}", "水景/泵", f"{desc}，{suf[1:] if suf else ''}。",
               "pumpform", variant, PV_PUMP, icon, tier, p))

    for variant, name, desc, icon, base, roleset in pumpforms + pump_surge:
        if roleset == "speed":
            for suf, sp, tier in SPEED_ROLES:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(speed=sp))
        elif roleset == "speeddir":
            for suf, sp, tier in SPEED_ROLES:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(speed=sp))
            for suf, dv, tier in DIR_ROLES[1:]:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(direction=dv))
        elif roleset == "freqdir":
            for suf, fq, tier in FREQ_ROLES:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(freq=fq))
            for suf, dv, tier in DIR_ROLES[1:3]:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(direction=dv))
        elif roleset == "widthdir":
            for suf, wd, tier in WIDTH_ROLES:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(width=wd))
            for suf, dv, tier in DIR_ROLES[:3]:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(direction=dv, width=base.get("width", 0.12)))
        elif roleset == "groups":
            for suf, gp, tier in GROUP_ROLES:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(groups=gp))
        elif roleset == "steps":
            for suf, st, tier in STEP_ROLES:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(steps=st))
        elif roleset == "heads":
            for suf, hd, tier in HEAD_ROLES:
                _pump_one(variant, name, desc, icon, base, suf, tier, dict(heads=hd))

    # ─── 水景/阀 — 门控场 + 跑阵 ───
    valvegates = [
        ("chop", "喷停", "全阀同步占空开合", "valve-chop", "duty"),
        ("chop_fast", "碎喷", "高速密集断续如机关枪", "valve-burst", "density"),
        ("dblclick", "双击喷", "每周期两击", "valve-dbl", "duty"),
        ("breach", "溃坝", "全开骤起渐收", "breach", "duty"),
        ("pulsetrain", "脉冲列喷", "节拍簇喷带间隙", "valve-train", "euclid"),
        ("flow_breath", "流量呼吸", "连续流量阀缓变", "flow-breath", "speed"),
        ("euclid", "欧氏喷律", "脉冲均匀分布的音乐喷律", "euclid-water", "euclid"),
        ("probgate", "概率门喷", "生成式随机触发", "dice-water", "density"),
        ("runner", "跑阀", "门窗沿阵列顺序跑", "valve-run", "widthdir"),
        ("wavewindow", "波窗扫", "软边开阀窗扫过宽幕", "wavewindow", "widthdir"),
        ("opposing", "对喷", "奇偶阀交替开成对射", "cross-water", "speed"),
        ("banks", "岸列轮替", "分组依次轮流开", "banks", "groups"),
        ("relay", "接力开阀", "一组开满下一组接力", "relay-water", "groups"),
        ("zipper", "拉链喷", "中心向两端依次开", "zipper-water", "dir"),
        ("stair_banks", "阶梯开岸", "分组阶梯逐级填满", "stair-banks", "groups"),
        ("headon", "对冲喷", "两端向中相遇开", "headon", "widthonly"),
        ("sparkle", "随机喷点", "确定性伪随机点喷", "sparkle-water", "density"),
    ]
    DUTY_ROLES = [("", 0.5, "基础")]  # duty is runtime param
    DENS_ROLES = [("", 0.4, "基础")]  # density is runtime param
    EUCLID_ROLES = [("", 5, 8, "中级")]  # pulses/steps are runtime params
    VSPEED_ROLES = [("", 1.2, "基础")]  # speed is runtime param

    def _valve_one(variant, name, desc, icon, suf, tier, ov):
        extra = []
        if variant in ("pulsetrain", "euclid") and "steps" in ov:
            extra = [ParamSpec("pulses", "脉冲数", "int", ov.get("pulses", 4), 1, 16, 1,
                tooltip="控制脉冲数量。值越大单个周期内产生的脉冲越密集"),
                     ParamSpec("steps", "步数", "int", ov.get("steps", 8), 2, 32, 1,
                         tooltip="控制步进/分段/级数。值越大离散阶梯越精细")]
        p = _valvegate_params(speed=ov.get("speed", 1.0), duty=ov.get("duty", 0.4),
                              width=ov.get("width", 0.15), groups=ov.get("groups", 3),
                              density=ov.get("density", 0.4), direction=ov.get("direction", "正向"),
                              extra=extra)
        add(_e(f"cur_vg_{variant}{suf2(suf)}", f"{name}{suf}", "水景/阀", f"{desc}，{suf[1:] if suf else ''}。",
               "valvegate", variant, PV_VALVE, icon, tier, p))

    for variant, name, desc, icon, roleset in valvegates:
        if roleset == "duty":
            for suf, dt, tier in DUTY_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(duty=dt))
            for suf, sp, tier in VSPEED_ROLES[1:]:
                _valve_one(variant, name, desc, icon, suf, tier, dict(speed=sp))
        elif roleset == "density":
            for suf, dn, tier in DENS_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(density=dn, speed=2.0))
        elif roleset == "euclid":
            for suf, pu, st, tier in EUCLID_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(pulses=pu, steps=st))
        elif roleset == "speed":
            for suf, sp, tier in VSPEED_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(speed=sp))
        elif roleset == "widthdir":
            for suf, wd, tier in WIDTH_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(width=wd, speed=1.2))
            for suf, dv, tier in DIR_ROLES[1:3]:
                _valve_one(variant, name, desc, icon, suf, tier, dict(direction=dv, speed=1.2))
        elif roleset == "widthonly":
            for suf, wd, tier in WIDTH_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(width=wd, speed=1.0))
        elif roleset == "groups":
            for suf, gp, tier in GROUP_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(groups=gp, speed=1.0))
        elif roleset == "dir":
            for suf, dv, tier in DIR_ROLES:
                _valve_one(variant, name, desc, icon, suf, tier, dict(direction=dv, speed=1.0))

    # ─── 水景/编程秀 — 组合 (pump+valve) ───
    combos = [
        ("peacock", "雀尾水炮", "阀扇形开+泵自中向缘渐增成孔雀尾", "peacock-water"),
        ("runner", "跑泉拖尾", "阀行进窗+泵窗头高度衰减拖尾", "runjet"),
        ("cascade", "此起彼伏炮", "分组阀开+泵涌组间错相", "swell"),
        ("arch", "水拱开合", "中心阀开+泵抛物拱呼吸", "arch"),
        ("salvo", "礼炮齐射", "全阀节拍齐开+泵爆涨急落", "watercannon"),
        ("crown", "皇冠加冕", "环列阀齐开+泵齐涌微相位", "crown-water"),
        ("wavewall", "起伏水墙", "泵行进高度+波谷自动断喷", "water-wall"),
        ("geyser", "间歇泉", "长蓄喷发冷却双通道联动", "geyser"),
    ]
    for variant, name, desc, icon in combos:
        for suf, sp, tier in [("·常态", 1.0, "高级"), ("·激越", 2.0, "高级")]:
            p = _watercombo_params(speed=sp)
            add(_e(f"cur_wc_{variant}{suf2(suf)}", f"{name}{suf}", "水景/编程秀",
                   f"{desc}，{suf[1:]}。", "watercombo", variant, PV_BOTH, icon, tier, p))

    # ─── 水景/编程秀 — 序列 ───
    shows = [
        ("overture", "喷泉序曲", "涌浪→水墙→皇冠→雀尾", "water-show"),
        ("runner_show", "跑泉秀", "跑阀→波窗→岸列→跑泉", "water-show"),
        ("cascade_show", "此起彼伏秀", "起伏→对涌→接力→起伏", "swell"),
        ("ode_water", "水之礼赞", "齐射→皇冠→雀尾→垂柳", "water-trophy"),
        ("tide_song", "潮汐之歌", "潮汐→涌浪→水墙→绽放", "tide-water"),
        ("peacock_show", "雀屏开合", "开屏→静拱→皇冠→收屏", "peacock-water"),
        ("geyser_show", "间歇喷涌", "间歇泉→涌浪→跑泉→潮汐", "geyser"),
        ("music_water", "音乐喷泉", "欧氏→喷停→跑阀→岸列", "water-music"),
        ("walldance", "水墙幻舞", "水墙→绽放→螺旋→静拱", "water-wall"),
        ("finale_water", "终曲水炮", "超级炮→齐射→皇冠→涌浪", "watercannon"),
        ("dawn_water", "破晓之泉", "涌浪→行拱→绽放→开屏", "water-show"),
        ("storm_water", "暴雨水阵", "碎喷→溃坝→波窗→对冲", "breach"),
        ("dragon_water", "蛟龙出海", "螺旋→多头跑→水墙→起伏", "spiral-water"),
        ("lotus_water", "莲华绽放", "绽放→皇冠→垂柳→开屏", "bloom-water"),
        ("march_water", "列阵喷涌", "岸列→接力→阶梯→起伏", "banks"),
        ("celebr_water", "庆典水炮", "齐射→超级炮→皇冠→开屏", "watercannon"),
    ]
    for variant, name, desc, icon in shows:
        p = _watershow_params()
        add(_e(f"cur_ws_{variant}", name, "水景/编程秀", f"{desc}（流畅串接）。",
               "watershow", variant, PV_BOTH, icon, "高级", p))
        p2 = _watershow_params(speed=0.13, hold=0.75)
        add(_e(f"cur_ws_{variant}_slow", f"{name}·缓演", "水景/编程秀",
               f"{desc}，停留更久仪式感。", "watershow", variant, PV_BOTH, icon, "高级", p2))

    return F


# ════════════════════════════════════════════════════════════════════════════
#  音频反应效果（真实音频驱动 — 无音频时静默，不合成假 BPM）
# ════════════════════════════════════════════════════════════════════════════
def build_audio_light_families():
    """音频→单通道亮度效果（节拍/能量/结构/水景）。所有效果 rate 实际控制时序。"""
    F = []
    add = F.append
    ALL = CH_TYPES

    # ── 节拍同步 ──
    beat_presets = [
        ("beat_sync", "节拍脉冲", "每拍触发亮闪+衰减，跟随真实音频节拍", "metronome", "基础",
         dict(rate=1.0, decay=0.25)),
        ("beat_chase", "节拍追逐", "每拍跳到下一台设备", "arrow-right-bar", "基础",
         dict(rate=1.0, decay=0.3)),
        ("beat_euclid", "欧氏律动", "脉冲均匀分布在真实拍网格上", "circle-dot", "中级",
         dict(rate=1.0, decay=0.3, pulses=5, steps=16)),
        ("beat_burst", "节拍爆发", "真实节拍检测触发全高喷射", "arrow-big-up", "中级",
         dict(rate=1.0, decay=0.15)),
        ("beat_heartbeat", "心跳律动", "真实音频节拍驱动的强弱双拍脉动", "heartbeat", "基础",
         dict(rate=1.0, decay=0.2)),
        ("beat_groove", "韵律摆动", "跟随真实音频节奏的摇摆律动", "wave-sine", "基础",
         dict(rate=1.0, decay=0.4)),
        ("beat_pulsetrain", "节拍脉冲列", "真实拍网格上的欧氏脉冲列喷射", "grip-horizontal", "中级",
         dict(rate=1.0, decay=0.2, pulses=5, steps=16)),
        ("audio_strobe", "音频频闪", "节拍触发瞬间全亮+快速衰减，仿频闪灯", "zap", "基础",
         dict(rate=2.0, decay=0.08)),
        ("audio_chase_energy", "能量跑灯", "RMS高→快跑，RMS低→慢跑，跟随能量变化", "run", "中级",
         dict(rate=1.0, decay=0.3, sensitivity=0.3)),
        ("audio_onset_spark", "起音火花", "强起音触发瞬间全亮+快速衰减", "sparkles", "基础",
         dict(rate=1.0, decay=0.1, sensitivity=0.4)),
    ]
    for variant, name, desc, icon, tier, ov in beat_presets:
        p = _audio_light_params(
            rate=ov.get("rate", 1.0),
            decay=ov.get("decay", 0.3),
            sensitivity=ov.get("sensitivity", 0.3),
        )
        if "pulses" in ov:
            p.append(ParamSpec("pulses", "脉冲数", "int", ov["pulses"], 1, 16, 1,
                tooltip="控制脉冲数量。值越大单个周期内产生的脉冲越密集"))
        if "steps" in ov:
            p.append(ParamSpec("steps", "步数", "int", ov["steps"], 2, 32, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"))
        add(_e(f"cur_audio_{variant}", name, "音频反应",
               desc, "audio_light", variant, ALL, icon, tier, p))

    # ── 能量跟随 ──
    energy_presets = [
        ("energy_follow", "能量起伏", "整体亮度跟随真实RMS能量", "activity", "基础",
         dict(rate=1.0, sensitivity=0.3)),
        ("energy_pump", "侧链泵感", "低音触发→快速衰减，仿压缩器sidechain", "wave-sine", "中级",
         dict(rate=1.0, sensitivity=0.3, pump_decay=0.15)),
        ("energy_range", "动态范围", "弱音暗、强音亮，含最低亮度地板", "trending-up", "基础",
         dict(rate=1.0, sensitivity=0.3, floor=0.2)),
        ("energy_geyser", "能量间歇泉", "RMS超阈值间歇喷发，低于阈值回落", "fountain", "中级",
         dict(rate=1.0, sensitivity=0.35)),
        ("audio_wave_sine", "音频正弦波", "RMS驱动正弦波亮度，强弱音振幅变化", "sine-wave", "基础",
         dict(rate=1.0)),
        ("audio_wave_triangle", "音频三角波", "BPM驱动三角波，设备间相位展开", "triangle", "基础",
         dict(rate=1.0, spread=0.3)),
        ("audio_fade_breath", "音频呼吸", "RMS驱动呼吸渐变，弱音暗、强音亮", "wind", "基础",
         dict(rate=0.5)),
        ("audio_band_scroll", "频段滚动", "低/中/高频段能量驱动设备三段亮度", "equalizer", "中级",
         dict(rate=1.0, spread=0.3)),
    ]
    for variant, name, desc, icon, tier, ov in energy_presets:
        p = _audio_light_params(
            rate=ov.get("rate", 1.0),
            sensitivity=ov.get("sensitivity", 0.3),
            spread=ov.get("spread", 0.0),
        )
        if "pump_decay" in ov:
            p.append(ParamSpec("pump_decay", "泵衰减", "float", ov["pump_decay"], 0.02, 0.5, 0.01,
                tooltip="控制水泵关闭后的衰减速度。值越大水泵余转时间越长"))
        if "floor" in ov:
            p.append(ParamSpec("floor", "最低亮度", "float", ov["floor"], 0.0, 0.8, 0.05,
                tooltip="控制底面/最低值。效果不会低于此值"))
        add(_e(f"cur_audio_{variant}", name, "音频反应",
               desc, "audio_light", variant, ALL, icon, tier, p))

    # ── 段落驱动（需 DeepAnalysis 预分析）──
    structure_presets = [
        ("structure_energy", "能量级联", "段落能量分位映射效果强度", "layers-difference", "高级",
         dict(rate=1.0)),
        ("structure_build_drop", "蓄力爆发", "build段渐强→drop段全开", "arrow-big-up", "高级",
         dict(rate=1.0)),
        ("structure_onset_chase", "起音追逐", "强起音触发步进到下一设备", "arrows-shuffle", "高级",
         dict(rate=1.0, sensitivity=0.45)),
        ("struct_music_water", "音乐喷泉秀", "跟随音乐段落能量的编程水秀", "water-show", "高级",
         dict(rate=1.0, sensitivity=0.3)),
    ]
    for variant, name, desc, icon, tier, ov in structure_presets:
        p = _audio_light_params(
            rate=ov.get("rate", 1.0),
            sensitivity=ov.get("sensitivity", 0.3),
        )
        add(_e(f"cur_audio_{variant}", name, "音频反应",
               desc, "audio_light", variant, ALL, icon, tier, p))

    return F


def build_audio_color_families():
    """音频→RGB 颜色效果（频谱颜色映射）。"""
    F = []
    add = F.append
    RGB_CH = ["R", "G", "B"]

    spectrum_presets = [
        ("spectrum_rainbow", "频谱彩虹", "频谱质心扫过色相环，跟随音频音色变化", "rainbow", "中级",
         dict(rate=0.3, brightness=1.0, spread=0.0)),
        ("spectrum_bands", "频段分色", "低=红/中=绿/高=蓝，按频段能量混色", "color-swatch", "中级",
         dict(rate=0.3, brightness=1.0)),
        ("spectrum_warmcool", "暖冷随音", "低频多→暖色，高频多→冷色，仿色温变化", "temperature", "基础",
         dict(rate=0.3, brightness=1.0)),
        ("spectrum_hsv_cycle", "色相旋转", "频谱质心驱动色相环连续旋转+设备展开", "palette", "中级",
         dict(rate=0.5, saturation=1.0, brightness=1.0, spread=0.3)),
        ("beat_color_pop", "节拍彩色", "每拍随机色相+拍内渐暗", "color-burst", "基础",
         dict(rate=1.0, saturation=1.0, brightness=1.0, spread=0.0)),
        ("audio_bicolor_beat", "双色节拍", "节拍在双色之间交替切换", "yin-yang", "基础",
         dict(rate=1.0, saturation=1.0, brightness=1.0)),
        ("audio_centroid_fire", "质心火焰", "低质心→火焰暖色，高质心→冰蓝冷色", "flame", "中级",
         dict(rate=0.3, brightness=1.0, spread=0.0)),
    ]
    for variant, name, desc, icon, tier, ov in spectrum_presets:
        p = _audio_color_params(
            rate=ov.get("rate", 0.3),
            sat=ov.get("saturation", 1.0),
            bright=ov.get("brightness", 1.0),
            spread=ov.get("spread", 0.0),
        )
        add(_e(f"cur_audio_{variant}", name, "音频反应",
               desc, "audio_color", variant, RGB_CH, icon, tier, p))

    return F


def build_audio_motor_families():
    """音频→电机轨迹效果。"""
    F = []
    add = F.append
    MOT = ["motor_x", "motor_y"]

    motor_presets = [
        ("motor_audio_circle", "音频圆圈", "圆圈半径跟随RMS，角速度跟随BPM", "circle", "中级",
         dict(rate=0.5, amp=0.8, spread=0.0)),
        ("motor_audio_spiral", "音频螺旋", "RMS驱动螺旋半径，BPM驱动旋转速度", "spiral", "高级",
         dict(rate=0.5, amp=1.0, spread=0.3)),
        ("motor_audio_shake", "音频抖动", "强拍触发随机位置抖动+衰减回位", "shake", "中级",
         dict(rate=1.0, amp=0.6, spread=0.0)),
        ("motor_audio_sweep", "音频扫摆", "RMS驱动水平扫摆幅度，BPM驱动频率", "arrow-right", "基础",
         dict(rate=0.5, amp=0.8, spread=0.3)),
    ]
    for variant, name, desc, icon, tier, ov in motor_presets:
        p = _audio_motor_params(
            rate=ov.get("rate", 0.5),
            amp=ov.get("amp", 1.0),
            spread=ov.get("spread", 0.0),
        )
        add(_e(f"cur_audio_{variant}", name, "音频反应",
               desc, "audio_motor", variant, MOT, icon, tier, p))

    return F


def build_audio_valve_families():
    """音频→阀门序列效果。"""
    F = []
    add = F.append
    VAL = ["valve"]

    valve_presets = [
        ("valve_beat_gate", "节拍阀门", "每拍开→快速衰减关", "valve-gate", "基础",
         dict(rate=1.0, decay=0.2)),
        ("valve_beat_chase", "阀门追逐", "每拍跳到下一个阀门", "arrow-right-bar", "基础",
         dict(rate=1.0, decay=0.3)),
        ("valve_onset_burst", "起音齐喷", "强起音触发全组阀门齐开+衰减", "burst", "中级",
         dict(rate=1.0, decay=0.25)),
    ]
    for variant, name, desc, icon, tier, ov in valve_presets:
        p = _audio_valve_params(
            rate=ov.get("rate", 1.0),
            decay=ov.get("decay", 0.3),
        )
        add(_e(f"cur_audio_{variant}", name, "音频反应",
               desc, "audio_valve", variant, VAL, icon, tier, p))

    return F


def build_audio_pump_families():
    """音频→泵/变频效果。"""
    F = []
    add = F.append
    P_CH = ["pump"]

    pump_presets = [
        ("pump_energy_surge", "能量激涌", "RMS驱动泵基值，onset触发尖峰喷射", "waves", "中级",
         dict(rate=1.0, sensitivity=0.3, spread=0.0)),
        ("pump_band_react", "频段分泵", "低/中/高频段能量分别驱动不同泵组", "equalizer", "中级",
         dict(rate=1.0, sensitivity=0.3, spread=0.3)),
        ("pump_beat_wave", "节拍泵浪", "节拍触发波浪沿设备序列传播", "wave-sine", "中级",
         dict(rate=1.0, sensitivity=0.3, spread=0.0)),
        ("pump_audio_gradient", "音频梯度", "RMS驱动设备高度渐变，弱→矮、强→高", "gradient", "基础",
         dict(rate=1.0, sensitivity=0.3, spread=0.3)),
    ]
    for variant, name, desc, icon, tier, ov in pump_presets:
        p = _audio_pump_params(
            rate=ov.get("rate", 1.0),
            sensitivity=ov.get("sensitivity", 0.3),
            spread=ov.get("spread", 0.0),
        )
        add(_e(f"cur_audio_{variant}", name, "音频反应",
               desc, "audio_pump", variant, P_CH, icon, tier, p))

    return F


_SUF_MAP = {}
_SUF_N = [0]


def suf2(s: str) -> str:
    """把中文后缀映射成稳定 ascii id 片段。"""
    if s not in _SUF_MAP:
        _SUF_N[0] += 1
        _SUF_MAP[s] = "s%d" % _SUF_N[0]
    return _SUF_MAP[s]
