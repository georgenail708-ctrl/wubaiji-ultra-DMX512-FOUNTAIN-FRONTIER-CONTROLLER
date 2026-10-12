# -*- coding: utf-8 -*-
"""programmer_automation — Programmer 轨迹曲线的 DAW 式自动化录制（R178）

四模式（对齐 DAW automation lane 语义；纯逻辑类 AutomationRecorder 无 Qt 依赖，可单测）：

  write : 恒写 —— 每个采样周期都把源值写入样本（旧「推杆录制」行为）。
  touch : 触碰 —— 源值变化幅度超过阈值后开始写；停写判据分两路：
          · 真实手势：录制源推杆的 sliderPressed/Released 直接喂
            recorder.force_touch()/force_release()——按住持续写、
            真松手即停（DAW 惯例 DAW touch 惯例）；
          · 兜底：无手势事件的源（MIDI/摇杆/通道实时值）仍走旧
            release_hold 秒值稳定超时近似"松手"。
  latch : 锁存 —— 起写条件同 touch，但一旦起写就持续写最后值直到 stop()
          （松手不停，需显式停止）。
  read  : 读取 —— 不写样本，只回放现有曲线（回放接线见 trajectory_replay
          的 programmer|master applier + 面板 play timer）。

UI 壳 AutomationControls（模式按钮组 / 录制源下拉 / 源推杆 / 录制按钮）由
build_recording_section(panel) 整体构建并挂回面板属性（_traj_rec_slider /
_traj_rec_btn 契约不变）—— programmer_panel 侧只保留一行接线，上帝类不加方法。

录制源扩展：源可以是「推杆」（旧行为）或「通道」——通道实时值通过监听面板既有
controlTrajectoryEvent（programmer|strip|<ct>）获得，不侵入 ChannelTypeRow。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from . import qascade_theme as _th
from .widget_kit import NumSlider, PanelSection, prop_row
from .trajectory_reduce import DEFAULT_RDP_EPSILON, reduce_recorded_samples
from .trajectory_snap_ui import build_reduce_row, build_snap_row

# ── 模式常量 ─────────────────────────────────────────────────────────────────
MODE_WRITE = "write"
MODE_TOUCH = "touch"
MODE_LATCH = "latch"
MODE_READ = "read"
AUTOMATION_MODES = (MODE_WRITE, MODE_TOUCH, MODE_LATCH, MODE_READ)
MODE_LABELS = {
    MODE_WRITE: "写入",
    MODE_TOUCH: "触碰",
    MODE_LATCH: "锁存",
    MODE_READ: "读取",
}
#: 模式 → tabler 图标名（全仓禁 emoji，图标统一 tabler 矢量）。
MODE_ICONS = {
    MODE_WRITE: "pencil",
    MODE_TOUCH: "pointer",
    MODE_LATCH: "lock",
    MODE_READ: "player-play",
}
MODE_TOOLTIPS = {
    MODE_WRITE: "写入：录制期间每个采样周期都写入源值（旧推杆录制行为）",
    MODE_TOUCH: "触碰：源值变化超过阈值后开始写入，值稳定（松手）后停写",
    MODE_LATCH: "锁存：触碰起写后持续写最后值，直到停止录制",
    MODE_READ: "读取：不录制；开始即回放当前曲线（经面板既有值链写回）",
}

# touch 起写阈值（归一值单位 0..1）与松手判稳时长（秒）。
DEFAULT_TOUCH_THRESHOLD = 0.02
DEFAULT_RELEASE_HOLD = 0.15


class AutomationRecorder:
    """四模式采样决策器（纯逻辑，无 Qt；伪时钟驱动即可单测）。

    feed(t, value) 返回本周期写入的 [t, value] 样本，或不写（None）。
    samples 是累积样本列表（调用方直接取走归一化）。
    """

    def __init__(self, mode: str = MODE_WRITE,
                 touch_threshold: float = DEFAULT_TOUCH_THRESHOLD,
                 release_hold: float = DEFAULT_RELEASE_HOLD):
        self._samples: List[List[float]] = []
        self._mode = MODE_WRITE
        self._touch_threshold = max(1e-6, float(touch_threshold))
        self._release_hold = max(0.0, float(release_hold))
        self._last_value: Optional[float] = None
        self._last_move_t = 0.0
        self._writing = False
        # _held=真实按下手势（sliderPressed）——物理状态独立于录制会话，
        # reset()/stop() 均不清它（只有 force_release 才抬手）。
        self._held = False
        self.mode = mode

    # ── 状态 ──
    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, m: str) -> None:
        self._mode = m if m in AUTOMATION_MODES else MODE_WRITE

    @property
    def samples(self) -> List[List[float]]:
        return self._samples

    @property
    def writing(self) -> bool:
        """touch/latch 当前是否处于写入窗口（write 恒 True、read 恒 False）。"""
        if self._mode == MODE_WRITE:
            return True
        if self._mode == MODE_READ:
            return False
        return self._writing

    def reset(self) -> None:
        self._samples = []
        self._last_value = None
        self._last_move_t = 0.0
        self._writing = False

    def stop(self) -> List[List[float]]:
        """停止：返回累积样本（latch 的持续写到此为止）。"""
        out = list(self._samples)
        self._writing = False
        return out

    # ── 真实手势（推杆 sliderPressed/Released）──
    def force_touch(self) -> None:
        """真实按下：touch/latch 置「手在推杆上」——按住即写、判稳超时抑制。"""
        if self._mode in (MODE_TOUCH, MODE_LATCH):
            self._held = True

    def force_release(self) -> None:
        """真实松手：_held 抬手；touch 立即停写，latch 按语义持续写最后值。"""
        self._held = False
        if self._mode == MODE_TOUCH:
            self._writing = False

    # ── 采样 ──
    def feed(self, t: float, value: float) -> Optional[List[float]]:
        t = float(t)
        v = max(0.0, min(1.0, float(value)))
        if self._mode == MODE_READ:
            return None
        if self._mode == MODE_WRITE:
            return self._append(t, v)
        return self._feed_touch_like(t, v)

    def _append(self, t: float, v: float) -> List[float]:
        sample = [t, v]
        self._samples.append(sample)
        self._last_value = v
        return sample

    def _feed_touch_like(self, t: float, v: float) -> Optional[List[float]]:
        if self._last_value is None:
            # 首帧只建立基线，不触发起写；例外：录制开始时手已按住推杆
            # （_held）→ touch 语义=按住即写。
            self._last_value = v
            self._last_move_t = t
            return self._append(t, v) if self._held else None
        moved = abs(v - self._last_value) >= self._touch_threshold
        if self._held:
            # 手在推杆上：持续写；值稳定不等于松手（判稳超时在持有期无效）。
            self._writing = True
            if moved:
                self._last_move_t = t
            return self._append(t, v)
        return self._feed_touch_timeout(t, v, moved)

    def _feed_touch_timeout(self, t: float, v: float, moved: bool) -> Optional[List[float]]:
        """无手势事件源（MIDI/摇杆/通道实时值）的 touch/latch：值稳定性判据。"""
        if not self._writing:
            if moved:
                self._writing = True
                self._last_move_t = t
                return self._append(t, v)
            self._last_value = v
            return None
        if moved:
            self._last_move_t = t
        elif self._mode == MODE_TOUCH and (t - self._last_move_t) > self._release_hold:
            # 松手近似（值稳定超时）：touch 停写；latch 继续写最后值。
            self._writing = False
            self._last_value = v
            return None
        return self._append(t, v)


# ── 面板侧薄壳 ───────────────────────────────────────────────────────────────
class AutomationControls(QObject):
    """录制自动化控件状态机：模式按钮组 + 录制源 + 通道实时值缓存。

    面板唯一需要的接口：
      automation_begin(auto)   —— 录制开始（复位 + 同步 UI 模式 + 刷新通道表）
      automation_tick(auto, t) —— 一个采样周期，返回 [t, v] 或 None
    """

    mode_changed = pyqtSignal(str)
    source_changed = pyqtSignal(str)

    def __init__(self, panel: QWidget, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._panel = panel
        self.recorder = AutomationRecorder()
        self._live_values: Dict[str, float] = {}   # ch_type → 最近 strip 值(0..255)
        self._channel_types: List[str] = []
        self._source = "slider"
        self._rdp_epsilon = DEFAULT_RDP_EPSILON    # 录制抽稀垂直容差（归一值）
        self._btn_group: Optional[QButtonGroup] = None
        self._source_combo: Optional[QComboBox] = None
        self._mode_buttons: Dict[str, QPushButton] = {}
        # 监听面板既有轨迹事件总线（programmer|strip|<ct>）取通道实时值——纯读取。
        try:
            panel.controlTrajectoryEvent.connect(self._on_traj_event)
        except Exception:
            pass

    # ── 事件 ──
    def _on_traj_event(self, key: str, value) -> None:
        parts = str(key or "").split("|")
        if len(parts) == 3 and parts[0] == "programmer" and parts[1] == "strip":
            try:
                self._live_values[parts[2]] = float(value)
            except (TypeError, ValueError):
                pass

    # ── 模式 ──
    def mode(self) -> str:
        return self.recorder.mode

    def set_mode(self, m: str) -> None:
        self.recorder.mode = m
        for key, btn in self._mode_buttons.items():
            btn.setChecked(key == self.recorder.mode)
        self.mode_changed.emit(self.recorder.mode)

    # ── 真实手势（推杆源才有；通道实时值/MIDI 无事件源，走超时兜底）──
    def on_source_press(self) -> None:
        """sliderPressed → touch/latch 立即起写并抑制判稳超时。"""
        if self._source == "slider":
            self.recorder.force_touch()

    def on_source_release(self) -> None:
        """sliderReleased → touch 立即停写；latch 语义照旧持续。"""
        if self._source == "slider":
            self.recorder.force_release()

    # ── 录制抽稀 ──
    def rdp_epsilon(self) -> float:
        return self._rdp_epsilon

    def set_rdp_epsilon(self, eps: float) -> None:
        try:
            self._rdp_epsilon = max(0.0, float(eps))
        except (TypeError, ValueError):
            self._rdp_epsilon = DEFAULT_RDP_EPSILON

    # ── 录制源 ──
    def source(self) -> str:
        return self._source

    def set_source(self, src: str) -> None:
        self._source = str(src or "slider")
        self.source_changed.emit(self._source)

    def channel_types(self) -> List[str]:
        return list(self._channel_types)

    def refresh_channels(self) -> None:
        """从面板通道行刷新可选通道表（录制开始前调用一次即可）。"""
        cts: List[str] = []
        try:
            rows = list(getattr(self._panel._ch_list, "_rows", []) or [])
            for row in rows:
                for ct in (getattr(row, "_ch_types", []) or []):
                    ct = str(ct)
                    if ct and ct not in cts:
                        cts.append(ct)
        except Exception:
            cts = []
        self._channel_types = cts
        self._rebuild_source_combo()

    def _rebuild_source_combo(self) -> None:
        combo = self._source_combo
        if combo is None:
            return
        prev = self._source
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("推杆（本页滑块）", "slider")
        labels = getattr(self._panel, "_TRAJ_CH_LABELS", {})
        for ct in self._channel_types:
            combo.addItem(f"{labels.get(ct, ct)} [{ct}]", f"ch:{ct}")
        idx = combo.findData(prev)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        self._source = str(combo.currentData() or "slider")

    def current_source_value(self) -> Optional[float]:
        """当前源的归一值[0,1]；通道尚无实时值时回退推杆（write 不空洞）。"""
        slider = getattr(self._panel, "_traj_rec_slider", None)
        slider_v = None
        if slider is not None:
            slider_v = slider.value() / 255.0
        if not self._source.startswith("ch:"):
            return slider_v
        v = self._live_values.get(self._source[3:])
        return (v / 255.0) if v is not None else slider_v

    def master_channel(self) -> str:
        """read 模式回放写回的目标通道（录制源选通道时）；推杆源 → 空串。"""
        if self._source.startswith("ch:"):
            return self._source[3:]
        return ""


# ── 面板接线辅助（programmer_panel 只调这两个）───────────────────────────────
def automation_begin(auto: Optional[AutomationControls]) -> None:
    """录制开始：复位样本、同步 UI 模式、刷新通道表。"""
    if auto is None:
        return
    auto.refresh_channels()
    auto.recorder.reset()


def automation_tick(auto: Optional[AutomationControls], t: float) -> Optional[List[float]]:
    """一个录制采样周期：取当前源值并按四模式决定是否写入。"""
    if auto is None:
        return None
    v = auto.current_source_value()
    if v is None:
        return None
    return auto.recorder.feed(t, v)


def automation_is_read(auto: Optional[AutomationControls]) -> bool:
    return auto is not None and auto.mode() == MODE_READ


def automation_reduce(auto: Optional[AutomationControls],
                      samples: List[List[float]]) -> List[List[float]]:
    """录制收尾的 RDP 抽稀（auto 缺失→默认容差；容差 0→原样）。"""
    eps = DEFAULT_RDP_EPSILON if auto is None else auto.rdp_epsilon()
    return reduce_recorded_samples(samples, eps)


def automation_read_step(panel) -> None:
    """read 模式回放一步（play timer 每 33ms 调一次，其余模式 no-op）：

    把当前曲线值经面板既有主值链写回（row._on_master_changed → 33ms 节流 →
    fader 层，推杆 UI 联动），同时发 programmer|master|<ct> 轨迹事件——
    clip 录制可捕获该键，回放走 trajectory_replay 的 master applier。
    目标行无法确定性解析（多候选/无候选）→ 跳过（不串台，与摇杆分发同策）。
    """
    try:
        auto = getattr(panel, "_traj_auto", None)
        if not automation_is_read(auto) or not getattr(panel, "_traj_rec_active", False):
            return
        ct = auto.master_channel()
        if not ct:
            return
        tr = panel._traj_selected()
        if tr is None:
            return
        value = max(0.0, min(1.0, float(tr.value_at(float(panel._traj_play_t)))))
        row = panel._find_joystick_target_row(ct, "", "")
        if row is None:
            return
        panel.controlTrajectoryEvent.emit(f"programmer|master|{ct}", int(round(value * 255.0)))
        row._on_master_changed(ct, value * 255.0, layer="fader")
    except Exception:
        pass


# ── UI 构建（整段从 _build_trajectory_page 搬出，上帝类只留一行接线）──────────
class _AutoSection(PanelSection):
    """与本页 _KitSection 同款的凹陷卡样式（不依赖上帝类模块，避免循环导入）。"""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(title, expanded, parent)
        self.setStyleSheet(
            "#flatSection {"
            f" background: {_th.BG_EDITOR};"
            " border: 1px solid rgba(255,255,255,17);"
            f" border-radius: {_th.RADIUS}px;"
            " }")
        self._body_lay.setContentsMargins(8, 4, 8, 8)
        self._body_lay.setSpacing(4)


def _build_mode_row(auto: AutomationControls) -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    group = QButtonGroup(row)
    group.setExclusive(True)
    for m in AUTOMATION_MODES:
        btn = make_icon_button(MODE_ICONS[m], MODE_LABELS[m], MODE_TOOLTIPS[m],
                               checkable=True)
        btn.setChecked(m == MODE_WRITE)
        btn.setFixedHeight(22)
        btn.clicked.connect(lambda _c=False, m=m: auto.set_mode(m))
        group.addButton(btn)
        lay.addWidget(btn)
        auto._mode_buttons[m] = btn
    lay.addStretch()
    auto._btn_group = group
    return row


def _build_source_row(auto: AutomationControls, panel: QWidget) -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    combo = QComboBox()
    combo.setFixedHeight(22)
    combo.setToolTip("录制源：推杆（本页滑块）或某通道类型的实时值（走推杆/摇杆值链）")
    auto._source_combo = combo
    auto.refresh_channels()
    combo.currentIndexChanged.connect(
        lambda _i: auto.set_source(str(combo.currentData() or "slider")))
    lay.addWidget(combo)
    # 源推杆 / 录制按钮沿用面板既有属性契约（_traj_rec_slider / _traj_rec_btn）。
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 255)
    slider.setValue(0)
    slider.setToolTip("录制源推杆：拖动写入录制样本（时长=曲线时长）；源选通道时仅作回退。\n"
                      "touch/latch 模式下按住即写、松手即停（真实手势事件）")
    # 真实手势事件喂 recorder（touch 松手即停；通道/MIDI 源无此事件，走超时兜底）。
    slider.sliderPressed.connect(auto.on_source_press)
    slider.sliderReleased.connect(auto.on_source_release)
    panel._traj_rec_slider = slider
    lay.addWidget(slider, 1)
    btn = QPushButton()
    btn.setFixedHeight(24)
    style_record_button(btn, recording=False)
    btn.clicked.connect(panel._traj_on_record_toggle)
    panel._traj_rec_btn = btn
    lay.addWidget(btn)
    return row


def build_recording_section(panel: QWidget) -> PanelSection:
    """构建「自动化录制」分区（模式/源/推杆/按钮 + 节拍吸附），挂 panel._traj_auto。"""
    auto = AutomationControls(panel)
    panel._traj_auto = auto
    sec = _AutoSection("自动化录制（模式 · 录制源 · 推杆，时长 = 曲线时长）")
    sec.add_widget(prop_row("模式", _build_mode_row(auto)))
    sec.add_widget(prop_row("录制源", _build_source_row(auto, panel)))
    sec.add_widget(prop_row("节拍吸附", build_snap_row(panel)))
    sec.add_widget(prop_row("录制抽稀", build_reduce_row(auto)))
    return sec


# ── 图标助手（R178：轨迹页图标统一 tabler，逻辑收本模块，上帝类只减不增）──────
def make_icon_button(icon_key: str, text: str, tooltip: str, *,
                     checkable: bool = False, height: int = 24,
                     color: str = "") -> QPushButton:
    """图标+短文字按钮（tabler 矢量；tooltip 承载完整语义，禁 emoji 字形）。"""
    from .main_icon_library import DEFAULT_ICON_COLOR, load_tabler_icon

    btn = QPushButton(text)
    btn.setIcon(load_tabler_icon(icon_key, 14, color or DEFAULT_ICON_COLOR))
    btn.setToolTip(tooltip)
    btn.setCheckable(checkable)
    btn.setFixedHeight(height)
    btn.setStyleSheet("QPushButton{font-size:11px;padding:3px 8px;}")
    return btn


def style_record_button(btn: QPushButton, recording: bool) -> None:
    """录制钮两态：待机=红点 tabler point；录制中=红停止方块（Recording 样式语义）。"""
    from .main_icon_library import RECORD_RED, load_tabler_icon

    if recording:
        btn.setText("停止录制")
        btn.setIcon(load_tabler_icon("player-stop", 14, RECORD_RED))
    else:
        btn.setText("开始录制")
        btn.setIcon(load_tabler_icon("point", 14, RECORD_RED))


def set_play_icon(btn: QPushButton, playing: bool) -> None:
    """transport 播放钮两态：播放三角 ↔ 暂停双竖（图标态，不拼字形文本）。"""
    from .main_icon_library import load_tabler_icon

    btn.setText("暂停" if playing else "播放")
    btn.setIcon(load_tabler_icon("player-pause" if playing else "player-play", 14))
