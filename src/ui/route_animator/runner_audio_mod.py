# -*- coding: utf-8 -*-
"""R7：跑动引擎的音频调制入口（音量/onset → 油门 target）。

深查③ R7：引擎侧没有音频入口。把**现成的** `audio_param_modulator` 调制链
（`ModRule.map_value`，linear/exp/log/step）接到跑动油门上：帧循环里把音频
特征映射为油门值，经 `set_throttle(..., ramp_sec=)` 写进斜坡 target
（R1 语义，替换/叠加两模式）——引擎零改动、零新依赖。

数据源（二选一，本模块闭环）：实时 `attach_audio_source`（懒连全局
MusicStructureAnalyzer.analysis_tick，弱引用槽）或离线 `feed_level` 注入。
优先级：在 `advance_envelopes` 之前跑（runner_panel._tick_impl）→ 显式包络
（R3）激活时覆盖音频调制（手动编排优先）。
"""
from __future__ import annotations
from ..editor_workspace_regions import popup
import logging
import weakref as _weakref

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from ..widget_kit import PanelSection, prop_row
from ..qascade_theme import TEXT_SECONDARY

log = logging.getLogger(__name__)

#: 场景字典里的键（`_save_scene` 写出 / `finish_scene_restore` 读回）
SCENE_KEY = "audio_mod"

#: 可选音频源（键 = AUDIO_SOURCES 子集 + 本模块的 "onset" 合成源）
SOURCE_CHOICES = (
    ("rms", "整体力度(RMS)"),
    ("onset", "起音(onset: 鼓组最大)"),
    ("peak", "峰值电平"),
    ("kick", "踢鼓"),
    ("snare", "军鼓"),
    ("hihat", "踩镲"),
    ("bass", "贝斯"),
    ("band_bass", "低频段"),
    ("bar_energy", "小节能量"),
    ("onset_density", "节奏密度"),
    ("flux", "频谱变化率"),
    ("bpm", "节拍速度"),
)

#: onset 合成源 = 鼓组在场度取最大（无独立 onset 字段，乐器 onset 现成）
ONSET_SOURCES = ("kick", "snare", "hihat")  # pylint: disable=invalid-name
CURVE_CHOICES = (("linear", "线性"), ("exp", "指数(压后段)"),
                 ("log", "对数(抬前段)"), ("step", "阶跃(过半数)"))
MODE_CHOICES = (("replace", "替换目标"), ("add", "叠加目标"))
DEFAULT_CONFIG = {
    "enabled": False,
    "source": "rms",
    "in_min": 0.0,
    "in_max": 1.0,
    "out_min": 0.0,
    "out_max": 1.0,
    "curve": "linear",
    "mode": "replace",
    "smooth": 0.2,
    "ramp_sec": 0.25,
    "easing": "OutCubic",
    "targets": "selected",   # selected=仅车队树选中；all=全部火车
}

#: 电平平滑状态（per panel id，避免给面板对象挂状态字典）
_LEVEL_STATE: dict = {}


def audio_mod_of(panel) -> dict:
    """面板的音频调制配置（懒建 = DEFAULT_CONFIG 副本 + 运行态电平）。"""
    cfg = getattr(panel, "_audio_mod_cfg", None)
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)
        cfg["_level"] = 0.0
        cfg["_level_smoothed"] = 0.0
        cfg["_applied"] = 0
        panel._audio_mod_cfg = cfg
    return cfg


def config_to_scene(panel) -> dict:
    """可持久化配置（去掉运行态键；未启用且未改过 → 空 dict 不落盘）。"""
    cfg = audio_mod_of(panel)
    out = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    if not out.get("enabled") and out == {**DEFAULT_CONFIG}:
        return {}
    return out


def apply_scene_config(panel, data) -> bool:
    """场景恢复：落盘配置灌回面板（坏数据忽略）。★ 清查⑧A P2-2：空 dict/缺键 =
    复位 DEFAULT_CONFIG（显式清零）——修前直接 return，从"开了调制的场景"切到
    "没配过的场景"时旧调制残留（与 sync_loop"无 loop 也清零"同款纪律）。
    """
    data = {} if data is None else data   # None 同义空配置：没配过 → 复位（P2-2）
    if not isinstance(data, dict):
        return False
    cfg = audio_mod_of(panel)
    for key, default in DEFAULT_CONFIG.items():
        if key in data:
            try:
                cfg[key] = type(default)(data[key]) if not isinstance(default, bool) \
                    else bool(data[key])
            except (TypeError, ValueError):
                cfg[key] = default
        else:
            cfg[key] = default      # 缺键 = 该场景没配过 → 回默认（关）
    return True


def set_config(panel, **kwargs) -> dict:
    """批量改配置（未知键忽略；数值钳制在安全范围）。"""
    cfg = audio_mod_of(panel)
    for key, value in kwargs.items():
        if key not in DEFAULT_CONFIG:
            continue
        try:
            cfg[key] = bool(value) if isinstance(DEFAULT_CONFIG[key], bool) else value
        except Exception:
            log.debug("audio mod config set failed (key=%s)", key, exc_info=True)
    return cfg


def feed_level(panel, value: float) -> None:
    """注入归一化电平（实时事件或离线测试；0..1 之外由映射段钳位）。"""
    audio_mod_of(panel)["_level"] = float(value or 0.0)


def _extract_sources(ev) -> dict:
    """复用现成调制引擎的特征提取（异常 = 空表，绝不打断帧循环）。"""
    global _MODULATOR
    try:
        if _MODULATOR is None:
            from ...audio.audio_param_modulator import AudioParamModulator
            _MODULATOR = AudioParamModulator()
        return _MODULATOR._extract_sources(ev)
    except Exception:
        log.debug("audio mod source extraction failed", exc_info=True)
        return {}


#: 特征提取单例（每帧调用，不能每次构造调制引擎）
_MODULATOR = None


def source_value(ev, name: str) -> float:
    """从事件取指定音频源值；`onset` = 鼓组在场度最大值（本模块合成）。"""
    values = _extract_sources(ev)
    if str(name) == "onset":
        return max((float(values.get(k, 0.0)) for k in ONSET_SOURCES),
                   default=0.0)
    return float(values.get(str(name), 0.0))


def update_from_event(panel, ev) -> float:
    """`analysis_tick` 槽：事件 → 当前源电平（写进配置的 `_level`）。"""
    cfg = audio_mod_of(panel)
    value = source_value(ev, cfg.get("source", "rms"))
    feed_level(panel, value)
    return value


def attach_audio_source(panel) -> bool:
    """懒连全局音乐结构分析器；无音频环境 → False（面板给提示，不改行为）。

    ★ 清查⑧A P1-3：槽走**弱引用 + destroyed 断连**——修前 lambda 强引用 panel
      且信号表在全局单例上 → 面板永不回收（5 次开关=5 死面板仍收 20Hz 回调）。
    """
    if getattr(panel, "_audio_mod_attached", False):
        return True
    try:
        from ...audio.music_structure_analyzer import get_global_structure_analyzer
        msa = get_global_structure_analyzer()
        ref = _weakref.ref(panel)

        def _slot(ev, _ref=ref):
            p = _ref()
            if p is None:
                return
            try:
                update_from_event(p, ev)
            except RuntimeError:
                pass    # 面板 C++ 侧已删（销毁竞态）→ 静默

        msa.analysis_tick.connect(_slot)
        try:
            panel.destroyed.connect(lambda _o=None, _m=msa, _s=_slot:
                                    _disconnect_slot(_m, _s))
        except Exception:
            log.debug("audio mod destroy-hook attach failed", exc_info=True)
    except Exception:
        log.debug("audio mod attach failed", exc_info=True)
        return False
    panel._audio_mod_attached = True
    return True


def _disconnect_slot(msa, slot) -> None:
    """断开全局分析器上的槽（destroyed 时调用；捕获引用不回查面板）。"""
    try:
        msa.analysis_tick.disconnect(slot)
    except Exception:
        log.debug("audio mod slot disconnect failed", exc_info=True)


def _rule_class():
    """现成的 ModRule（曲线映射 map_value 唯一实现，不另写一份）。"""
    from ...audio.audio_param_modulator import ModRule
    return ModRule


def map_level(cfg: dict, raw: float) -> float:
    """原始特征 → 油门值（ModRule 曲线映射；出范围由调用方钳 [0,1]）。"""
    try:
        rule = _rule_class()(
            source=str(cfg.get("source", "rms")),
            param_key="throttle",
            out_min=float(cfg.get("out_min", 0.0)),
            out_max=float(cfg.get("out_max", 1.0)),
            in_min=float(cfg.get("in_min", 0.0)),
            in_max=float(cfg.get("in_max", 1.0)),
            curve=str(cfg.get("curve", "linear")),
        )
        return float(rule.map_value(float(raw)))
    except Exception:
        log.debug("audio mod map failed", exc_info=True)
        return 0.0


def smooth_level(cfg: dict, raw: float, dt: float) -> float:
    """一阶指数平滑（沿用 ModRule.smooth 语义：0=不滑，→1=锁死）。"""
    alpha = max(0.0, min(1.0, float(cfg.get("smooth", 0.0))))
    prev = float(cfg.get("_level_smoothed", raw))
    if alpha <= 0.0 or dt <= 0.0:
        cfg["_level_smoothed"] = raw
        return raw
    # dt 归一：帧长 1/60s 为基准，实际帧长按比例折算（不随帧率漂移）
    k = min(1.0, dt * 60.0)
    alpha_k = alpha ** k
    value = prev * alpha_k + raw * (1.0 - alpha_k)
    cfg["_level_smoothed"] = value
    return value


def target_train_ids(panel, cfg: dict) -> list:
    """调制目标：选中集优先（"selected" 且确有选中），否则全部火车。"""
    trains = getattr(getattr(panel, "_engine", None), "_trains", None) or {}
    if str(cfg.get("targets", "selected")) == "selected":
        try:
            picked = [t for t in panel._fleet_selected_train_ids() if str(t) in trains]
        except Exception:
            picked = []
        if picked:
            return picked
    return list(trains.keys())


def _write_one(engine, cfg: dict, tid: str, mapped: float) -> bool:
    """单车油门写入（替换/叠加两种模式；失败只记日志不打断帧循环）。"""
    tr = engine._trains.get(str(tid))
    if tr is None:
        return False
    if str(cfg.get("mode", "replace")) == "add":
        base = float(getattr(tr, "throttle_target", tr.throttle) or 0.0)
        value = base + mapped
    else:
        value = mapped
    try:
        engine.set_throttle(str(tid), max(0.0, min(1.0, value)),
                            ramp_sec=float(cfg.get("ramp_sec", 0.0) or 0.0),
                            easing=str(cfg.get("easing", "OutCubic")))
    except Exception:
        log.debug("audio mod throttle write failed (tid=%s)", tid, exc_info=True)
        return False
    return True


def advance_audio_modulation(panel, dt: float) -> int:
    """帧循环钩子：按配置把所有目标火车的油门 target 写成音频映射值。

    返回实际写入的车数（未启用/无车 → 0，零开销）。写油门走 ``set_throttle``
    的 ramp 通道——斜坡由 R1 管线缓慢逼近，不会给物理灯/泵速度阶跃。
    """
    cfg = audio_mod_of(panel)
    engine = getattr(panel, "_engine", None)
    if not cfg.get("enabled") or engine is None:
        return 0
    raw = smooth_level(cfg, float(cfg.get("_level", 0.0)), dt)
    mapped = map_level(cfg, raw)
    applied = sum(1 for tid in target_train_ids(panel, cfg)
                  if _write_one(engine, cfg, tid, mapped))
    cfg["_applied"] = applied
    return applied


class AudioModDialog(QDialog):
    """音频调制设置浮窗（音频源 / 范围 / 曲线 / 替换-叠加 / 斜坡 + 实时电平）。"""

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent or panel)
        self._panel = panel
        self.setWindowTitle("音频调制 → 油门")
        self.resize(520, 430)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        tip = QLabel("音频特征经曲线映射为油门目标：替换 = 直接覆盖目标值；"
                     "叠加 = 在当前目标值上加。写入走斜坡(节拍跟随不跳变)。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        root.addWidget(tip)

        sec = PanelSection("映射设置")
        self._enable = QCheckBox("启用音频调制")
        self._enable.toggled.connect(self._on_enabled)
        sec.add_widget(self._enable)

        self._source = QComboBox()
        for key, label in SOURCE_CHOICES:
            self._source.addItem(label, key)
        self._source.currentIndexChanged.connect(self._on_changed)
        sec.add_widget(prop_row("音频源", self._source))

        self._mode = QComboBox()
        for key, label in MODE_CHOICES:
            self._mode.addItem(label, key)
        self._mode.currentIndexChanged.connect(self._on_changed)
        sec.add_widget(prop_row("方式", self._mode))

        self._curve = QComboBox()
        for key, label in CURVE_CHOICES:
            self._curve.addItem(label, key)
        self._curve.currentIndexChanged.connect(self._on_changed)
        sec.add_widget(prop_row("曲线", self._curve))

        self._lo, self._hi = self._make_range_row(sec, "油门范围")
        self._ramp, self._smooth = self._make_time_row(sec)
        self._targets = QComboBox()
        self._targets.addItem("仅车队树选中", "selected")
        self._targets.addItem("全部火车", "all")
        self._targets.currentIndexChanged.connect(self._on_changed)
        sec.add_widget(prop_row("作用对象", self._targets))
        root.addWidget(sec)

        lev = PanelSection("实时电平（无音频时可用滑杆离线试跑）")
        self._level = QSlider(Qt.Orientation.Horizontal)
        self._level.setRange(0, 100)
        self._level.valueChanged.connect(
            lambda v, p=panel: feed_level(p, v / 100.0))
        lev.add_widget(self._level)
        self._readout = QLabel("电平 0% · 映射油门 0% · 已写 0 列")
        self._readout.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        lev.add_widget(self._readout)
        root.addWidget(lev)

        btns = QHBoxLayout()
        attach = QPushButton("连接音频分析器")
        attach.setFixedHeight(22)
        attach.setToolTip("懒连全局音乐结构分析器 analysis_tick（未播放音频则不生效）")
        attach.clicked.connect(self._on_attach)
        btns.addWidget(attach)
        apply_btn = QPushButton("应用并保存")
        apply_btn.setFixedHeight(22)
        apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(apply_btn)
        btns.addStretch()
        close = QPushButton("关闭")
        close.setFixedHeight(22)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        w = QWidget()
        w.setLayout(btns)
        root.addWidget(w)
        self._load()
        self._timer = None

    def _make_range_row(self, sec, label: str):
        row = QHBoxLayout()
        row.setSpacing(6)
        lo = QDoubleSpinBox(); lo.setRange(0.0, 1.0); lo.setDecimals(2); lo.setSingleStep(0.05)
        hi = QDoubleSpinBox(); hi.setRange(0.0, 1.0); hi.setDecimals(2); hi.setSingleStep(0.05)
        for s in (lo, hi):
            s.valueChanged.connect(self._on_changed)
        row.addWidget(QLabel("下限")); row.addWidget(lo)
        row.addWidget(QLabel("上限")); row.addWidget(hi)
        row.addStretch()
        holder = QWidget(); holder.setLayout(row)
        sec.add_widget(prop_row(label, holder))
        return lo, hi

    def _make_time_row(self, sec):
        row = QHBoxLayout()
        row.setSpacing(6)
        ramp = QDoubleSpinBox(); ramp.setRange(0.0, 10.0); ramp.setDecimals(2)
        ramp.setSingleStep(0.05)
        ramp.setToolTip("油门斜坡秒数：调制值经 R1 缓动管线逼近（0=立即）")
        smooth = QDoubleSpinBox(); smooth.setRange(0.0, 0.95); smooth.setDecimals(2)
        smooth.setSingleStep(0.05)
        smooth.setToolTip("电平平滑：0=不滑，→0.95=锁死（沿用 ModRule.smooth 语义）")
        for s in (ramp, smooth):
            s.valueChanged.connect(self._on_changed)
        row.addWidget(QLabel("斜坡s")); row.addWidget(ramp)
        row.addWidget(QLabel("平滑")); row.addWidget(smooth)
        row.addStretch()
        holder = QWidget(); holder.setLayout(row)
        sec.add_widget(prop_row("时间", holder))
        return ramp, smooth

    # ── 数据流 ───────────────────────────────────────────────────────────
    def _load(self) -> None:
        cfg = audio_mod_of(self._panel)
        for widget, key in ((self._source, "source"), (self._mode, "mode"),
                            (self._curve, "curve"), (self._targets, "targets")):
            widget.blockSignals(True)
            idx = widget.findData(cfg.get(key))
            widget.setCurrentIndex(idx if idx >= 0 else 0)
            widget.blockSignals(False)
        for spin, key in ((self._lo, "out_min"), (self._hi, "out_max"),
                          (self._ramp, "ramp_sec"), (self._smooth, "smooth")):
            spin.blockSignals(True)
            spin.setValue(float(cfg.get(key, 0.0) or 0.0))
            spin.blockSignals(False)
        self._enable.blockSignals(True)
        self._enable.setChecked(bool(cfg.get("enabled")))
        self._enable.blockSignals(False)
        self.refresh()

    def _on_changed(self, *_args) -> None:
        set_config(self._panel,
                   source=str(self._source.currentData() or "rms"),
                   mode=str(self._mode.currentData() or "replace"),
                   curve=str(self._curve.currentData() or "linear"),
                   targets=str(self._targets.currentData() or "selected"),
                   out_min=float(self._lo.value()), out_max=float(self._hi.value()),
                   ramp_sec=float(self._ramp.value()), smooth=float(self._smooth.value()))
        self.refresh()

    def _on_enabled(self, on: bool) -> None:
        set_config(self._panel, enabled=bool(on))
        self.refresh()

    def _on_attach(self) -> None:
        ok = attach_audio_source(self._panel)
        self._readout.setText("已连接音频分析器" if ok
                              else "连接失败：无可用音频分析器（用滑杆离线试跑）")

    def _on_apply(self) -> None:
        self._panel._save_scene(getattr(self._panel, "_current_page_id", None))
        self._readout.setText("配置已随当前页场景保存")

    def refresh(self) -> None:
        """刷新实时读数（定时器每 100ms 调；也供离屏探针直接调）。"""
        cfg = audio_mod_of(self._panel)
        level = float(cfg.get("_level", 0.0))
        mapped = map_level(cfg, float(cfg.get("_level_smoothed", level)))
        self._readout.setText(
            f"电平 {level * 100:.0f}% · 映射油门 {max(0.0, min(1.0, mapped)) * 100:.0f}%"
            f" · 已写 {int(cfg.get('_applied', 0))} 列")
        if not cfg.get("enabled"):
            self._readout.setText(self._readout.text() + "（未启用）")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._timer is None:
            from PyQt6.QtCore import QTimer
            self._timer = QTimer(self)
            self._timer.setInterval(100)
            self._timer.timeout.connect(self.refresh)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event) -> None:
        if self._timer is not None:
            self._timer.stop()
        super().hideEvent(event)


def open_audio_mod_dialog(panel) -> None:
    """面板按钮入口：单例浮窗（随面板销毁）。"""
    dlg = getattr(panel, "_audio_mod_dlg", None)
    if dlg is None:
        dlg = popup(AudioModDialog(panel))
        panel._audio_mod_dlg = dlg
        dlg.destroyed.connect(lambda: setattr(panel, "_audio_mod_dlg", None))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    dlg.refresh()


__all__ = ["advance_audio_modulation", "attach_audio_source", "audio_mod_of",
           "apply_scene_config", "config_to_scene", "feed_level", "map_level",
           "open_audio_mod_dialog", "set_config", "source_value",
           "target_train_ids", "update_from_event", "AudioModDialog",
           "SCENE_KEY", "DEFAULT_CONFIG"]
