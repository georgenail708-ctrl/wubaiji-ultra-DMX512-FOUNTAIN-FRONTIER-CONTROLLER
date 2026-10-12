# -*- coding: utf-8 -*-
"""clip 拖动**网格吸附**（snap）：Show Manager 公式直译 + 分级网格。

来源（借语义 + 可移植公式）：`ui/src/showmanager/multitrackview.cpp:601-606`
    ``gridPos = ((int)(x / step) * step)``  —— 时间域同形：``t = int(t/step)*step``。
    分级（秒 / 帧 / 小节 / 关闭）+ 修饰键临时旁路 = Arrangement View 的
    snap 等级 + Alt 临时禁用手势。

为什么独立小模块：clip_view.py / timeline_widget.py 均为基线冻结的既有大文件，
新逻辑按任务约束落新小模块；本模块零 Qt Widget 依赖（QSettings 除外），可无 GUI 单测。

持久化：QSettings("Qascade", "DMXTimeline") `timeline/snap_mode`（与
track_list 文件夹缩进同一组织/应用命名空间）。
"""
from __future__ import annotations

#: 吸附档位（值即 QSettings 存储串）
SNAP_OFF = "off"        # 关闭（自由拖动）
SNAP_SEC = "sec"        # 1 秒网格
SNAP_FRAME = "frame"    # 1 帧网格（fps 取工程 timecode_fps）
SNAP_BAR = "bar"        # 1 小节网格（bpm 折算：4 拍 × 60/bpm）

#: 全部合法档位（工具栏下拉/校验共用；顺序即 UI 顺序）
SNAP_MODES = (SNAP_OFF, SNAP_SEC, SNAP_FRAME, SNAP_BAR)

#: 档位 → 显示名（工具栏下拉）
SNAP_LABELS = {
    SNAP_OFF: "吸附:关",
    SNAP_SEC: "吸附:秒",
    SNAP_FRAME: "吸附:帧",
    SNAP_BAR: "吸附:小节",
}

_QS_KEY = "timeline/snap_mode"
_DEFAULT_MODE = SNAP_OFF
#: bpm / fps 的回退值（工程字段缺失/非法时；与 models.Project 默认一致）
DEFAULT_BPM = 120.0
DEFAULT_FPS = 25.0

#: **命中判定阈值（像素）** —— 全时间线唯一一份（拖动/裁剪/切割三种手势共用）。
#: 吸附落位与手势原始落位的像素差 ≥ 本值才算"吸附命中"：命中才画参考线、
#: 才算真的被网格拉住；差值小于本值说明吸附是空操作（取整后位置没变），
#: 画线只会造成"线在闪但位置没动"的噪声。
#: 的同一常量是 `multitrackview.cpp` 里 `shift < 3`（3px 内不移动）——
#: 本仓取亚像素级 0.6px，因为吸附是**连续**应用（非"拖够 3px 才生效"）。
SNAP_HIT_EPS_PX = 0.6


def normalize_mode(mode) -> str:
    """非法/缺失档位回落（QSettings 里可能被外部写坏）。"""
    m = str(mode or "").strip().lower()
    return m if m in SNAP_MODES else _DEFAULT_MODE


def load_snap_mode() -> str:
    """读取持久化的吸附档位；QSettings 不可用时回落默认档。"""
    try:
        from PyQt6.QtCore import QSettings
        return normalize_mode(QSettings("Qascade", "DMXTimeline").value(_QS_KEY))
    except Exception:
        return _DEFAULT_MODE


def save_snap_mode(mode: str) -> None:
    """持久化吸附档位（失败静默——偏好存不进去不影响功能）。"""
    try:
        from PyQt6.QtCore import QSettings
        QSettings("Qascade", "DMXTimeline").setValue(_QS_KEY, normalize_mode(mode))
    except Exception:
        pass


def _positive_or(v, default: float) -> float:
    """取正浮点；非法/NaN/<=0 → `default`（手写 NaN 检查：`nan<=0` 是 False）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float(default)
    if f != f or f <= 0.0:
        return float(default)
    return f


def snap_step_seconds(mode, bpm: float = DEFAULT_BPM,
                      fps: float = DEFAULT_FPS) -> float:
    """档位 → 网格步长（秒）。off/非法 → 0（= 不吸附）。

    · sec   → 1.0 秒；
    · frame → 1/fps（fps 非法/NaN/<=0 回落 DEFAULT_FPS）；
    · bar   → 4 拍 × 60/bpm（bpm 非法/NaN/<=0 回落 DEFAULT_BPM）。
    """
    m = normalize_mode(mode)
    if m == SNAP_SEC:
        return 1.0
    if m == SNAP_FRAME:
        return 1.0 / _positive_or(fps, DEFAULT_FPS)
    if m == SNAP_BAR:
        return 4.0 * 60.0 / _positive_or(bpm, DEFAULT_BPM)
    return 0.0


def snap_time(t: float, mode, bpm: float = DEFAULT_BPM,
              fps: float = DEFAULT_FPS, bypass: bool = False) -> float:
    """把时间 t 吸附到网格（公式直译，12 行的"单函数"落点）。

    · bypass=True（Alt 按住）或 off 档或步长<=0 → 原样返回；
    · 吸附取**向下取整**（``int(t/step)*step``，与 一致）并钳非负；
    · 负输入按 0 处理（clip 起点本就钳非负）。
    """
    if bypass:
        return max(0.0, float(t))
    step = snap_step_seconds(mode, bpm=bpm, fps=fps)
    if step <= 0.0:
        return max(0.0, float(t))
    return max(0.0, float(int(float(t) / step) * step))


def _as_float(v, default: float) -> float:
    """数值归一化（非法/NaN/None → `default`）；供手势上下文取值用。"""
    try:
        out = float(v)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if out != out else out


def snap_context(item):
    """从时间线 item 反查 `(mode, bpm, fps)`：工程字段缺失一律回落模块默认。

    上下文只有一处定义，三种手势（拖动/裁剪/切割）与参考线共用，不会各读一份。
    """
    bpm, fps = DEFAULT_BPM, DEFAULT_FPS
    root = None
    try:
        root = getattr(item, "_root_ref", None) or item._find_timeline_root()
    except Exception:
        root = None
    if root is not None:
        try:
            item._root_ref = root
        except Exception:
            pass
        sess = getattr(root, "_session", None)
        bpm = _as_float(getattr(sess, "bpm", DEFAULT_BPM), DEFAULT_BPM)
        fps = _as_float(getattr(sess, "timecode_fps", DEFAULT_FPS), DEFAULT_FPS)
    return load_snap_mode(), bpm, fps


def snap_move_start(item, t: float, alt: bool = False) -> float:
    """**拖动**手势落位：Alt（临时旁路）→ 原值；否则按当前档吸起点。"""
    if alt:
        return max(0.0, _as_float(t, 0.0))
    mode, bpm, fps = snap_context(item)
    return snap_time(t, mode, bpm=bpm, fps=fps)


def snap_resize_end(item, start, raw_end, alt: bool = False,
                    min_dur: float = 0.1) -> float:
    """**裁剪**手势（拖右缘）落位：吸的是**终点**，返回新时长（与拖动同公式）。"""
    if alt:
        return max(0.0, _as_float(raw_end, 0.0) - max(0.0, _as_float(start, 0.0)))
    mode, bpm, fps = snap_context(item)
    return snap_trim(start, raw_end, mode, bpm=bpm, fps=fps, min_dur=min_dur)


def snap_guide_time(item, raw: float, snapped: float):
    """吸附**命中**时该网格位置的秒数（画参考线用）；未命中/关档 → None。"""
    px = _as_float(getattr(item, "_px_per_sec", 0.0), 0.0)
    if not is_snap_hit(raw, snapped, px):
        return None
    mode, _bpm, _fps = snap_context(item)
    return None if normalize_mode(mode) == SNAP_OFF else _as_float(snapped, 0.0)


def snap_trim(t_start: float, raw_end: float, mode, *, bpm: float = DEFAULT_BPM,
              fps: float = DEFAULT_FPS, bypass: bool = False,
              min_dur: float = 0.1) -> float:
    """**裁剪手势**（拖右缘）的落位：把"被拖的那条边"吸到网格，返回新时长。

    与拖动同一条公式（`snap_time`）——差别只在坐标系：拖动吸**起点**，
    裁剪吸**终点**（`start + duration`），故先把终点吸到网格再换算回时长。
    终点吸附后小于 `start + min_dur`（含旁路/非吸附档导致的回退）时钳到最小
    时长，保证裁剪永不把 clip 缩成负数或零。
    """
    try:
        start = max(0.0, float(t_start))
    except (TypeError, ValueError):
        start = 0.0
    try:
        floor = start + max(0.0, float(min_dur))
    except (TypeError, ValueError):
        floor = start + 0.1
    end = snap_time(raw_end, mode, bpm=bpm, fps=fps, bypass=bypass)
    if end < floor:
        return max(0.0, floor - start)
    return max(0.0, end - start)


def is_snap_hit(raw: float, snapped: float, px_per_sec: float,
                eps_px: float = SNAP_HIT_EPS_PX) -> bool:
    """本手势是否**真的被网格拉住**（像素差 ≥ `SNAP_HIT_EPS_PX`）——决定画不画参考线。

    非法输入（px_per_sec<=0 / NaN）→ False（宁可不画，不画错位置的线）。
    """
    try:
        raw_f, snap_f, px = float(raw), float(snapped), float(px_per_sec)
    except (TypeError, ValueError):
        return False
    if px <= 0.0 or px != px or raw_f != raw_f or snap_f != snap_f:
        return False
    return abs(raw_f - snap_f) * px >= float(eps_px)


def next_snap_mode(mode) -> str:
    """工具栏按钮：当前档 → 下一档（顺序即 `SNAP_MODES`，循环）。"""
    m = normalize_mode(mode)
    try:
        i = SNAP_MODES.index(m)
    except ValueError:
        return _DEFAULT_MODE
    return SNAP_MODES[(i + 1) % len(SNAP_MODES)]


__all__ = ["SNAP_MODES", "SNAP_OFF", "SNAP_SEC", "SNAP_FRAME", "SNAP_BAR",
           "SNAP_LABELS", "normalize_mode", "load_snap_mode", "save_snap_mode",
           "snap_step_seconds", "snap_time", "snap_trim", "is_snap_hit",
           "snap_context", "snap_move_start", "snap_resize_end",
           "snap_guide_time", "next_snap_mode",
           "DEFAULT_BPM", "DEFAULT_FPS", "SNAP_HIT_EPS_PX"]
