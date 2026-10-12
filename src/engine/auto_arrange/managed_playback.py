"""
managed_playback — 一键托管运行时协调器

把"编曲总指挥"的决策接到真实输出通道，像人一样全盘操控：
  - effect 子系统（灯/泵/电机/阀）→ 通过 MusicToEffectBridge 应用 preset + 角色级 level
  - 电机/泵 solo → deep_aqua solo 标志（走路径/跟旋律）
  - matrix（矩阵像素）→ MatrixPixelEngine 模式切换
  - 留白 → 对应子系统静默

设计为"适配器注入"：协调器本身是纯逻辑，外部 UI/引擎通过回调注入实际动作，
因此可在无 GUI 下单测。面板只是它的薄壳。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any

from .project_profile import ProjectProfile, build_project_profile
from .arrangement_director import (
    ArrangementDirector, ArrangementDecision, MusicCue,
)
from .experience_table import learn_from_project, SUB_LIGHT, SUB_PUMP, SUB_MOTOR, SUB_VALVE, ROLE_SILENT


@dataclass
class ManagedAdapters:
    """外部注入的动作回调。任一为 None 则该路径跳过（容错）。"""
    # 应用某子系统的 effect：(subsystem, preset_id, level, group_ids, speed, solo_path)
    apply_subsystem: Optional[Callable[[str, str, float, List[str], float, bool], None]] = None
    # 静默某子系统
    silence_subsystem: Optional[Callable[[str], None]] = None
    # 设置 matrix 模式：(mode|None)  None=关闭 matrix
    set_matrix: Optional[Callable[[Optional[str]], None]] = None
    # 设置 solo 标志：(subsystem, on)
    set_solo: Optional[Callable[[str, bool], None]] = None
    # 实时消息：(text)
    message: Optional[Callable[[str], None]] = None


class ManagedPlayback:
    """一键托管协调器。"""

    def __init__(self, project: Any, adapters: Optional[ManagedAdapters] = None,
                 ml_manager: Any = None, bandit: Any = None):
        self._project = project
        self.adapters = adapters or ManagedAdapters()
        self._ml_manager = ml_manager
        self._bandit = bandit
        self.profile: Optional[ProjectProfile] = None
        self.director: Optional[ArrangementDirector] = None
        self._running = False
        self._last_section: Optional[str] = None
        self._last_solo: Optional[bool] = None
        self._last_decision: Optional[ArrangementDecision] = None
        # ── 预烘焙回放模式 ──
        self._baked = None                  # BakedArrangement（回放模式下持有）
        self._baked_seg_idx: Optional[int] = None   # 当前已应用的段落下标（避免重复应用）

    # ── 预烘焙回放模式 ────────────────────────────────────────────────
    def start_baked(self, plan) -> bool:
        """以预烘焙方案进入确定性回放模式。不做任何实时分析，
        只按播放位置查表应用既定决策。"""
        if plan is None or not getattr(plan, "segments", None):
            self._msg("无可用编曲方案，无法回放")
            return False
        self._baked = plan
        self._baked_seg_idx = None
        self._running = True
        self._msg("回放预烘焙编曲 · " + plan.summary_text())
        return True

    @property
    def is_baked(self) -> bool:
        return self._baked is not None

    def on_playhead(self, pos: float):
        """回放模式：按播放位置查段，段落切换时应用该段决策。
        纯查表，无重型分析，不卡主线程。回到起点(pos小)自然回到第一段。"""
        if not self._running or self._baked is None:
            return
        seg = self._baked.segment_at(float(pos))
        if seg is None:
            return
        # 用段落下标判定是否切换（同段不重复应用，避免每 tick 重发）
        try:
            idx = self._baked.segments.index(seg)
        except ValueError:
            idx = None
        if idx == self._baked_seg_idx:
            return
        self._baked_seg_idx = idx
        if seg.decision is not None:
            self._last_decision = seg.decision
            self._apply_decision(seg.decision)
            self._msg(f"[{seg.t_start:.0f}s] {seg.decision.explain}")

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self) -> bool:
        """托管开始：扫项目建画像 + 学经验 + 建总指挥。"""
        try:
            self.profile = build_project_profile(self._project)
            learned = learn_from_project(self._project)
            self.director = ArrangementDirector(
                self.profile, learned,
                ml_manager=self._ml_manager,
                bandit=self._bandit,
            )
        except Exception as e:
            self._msg(f"托管启动失败：{e}")
            return False
        self._running = True
        self._last_section = None
        self._last_solo = None
        self._msg("托管已启动 · 画像：" + self.profile.summary_text())
        self._msg("等待播放音频以识别段落…")
        return True

    def stop(self):
        self._running = False
        self._baked = None
        self._baked_seg_idx = None
        # 全部静默
        for sub in (SUB_LIGHT, SUB_PUMP, SUB_MOTOR, SUB_VALVE):
            self._silence(sub)
        if self.adapters.set_matrix:
            try:
                self.adapters.set_matrix(None)
            except Exception:
                pass
        self._msg("托管已停止")

    @property
    def running(self) -> bool:
        return self._running

    # ── 每次音乐上下文更新时调用（段落变化时触发重决策）──────────────
    def on_music_cue(self, cue: MusicCue):
        """喂入当前音乐上下文。仅在段落或 solo 状态变化时重新编排，避免抖动。"""
        if not self._running or self.director is None:
            return
        changed = (cue.section != self._last_section) or (cue.is_solo != self._last_solo)
        if not changed:
            return
        self._last_section = cue.section
        self._last_solo = cue.is_solo
        decision = self.director.decide(cue)
        self._last_decision = decision
        self._apply_decision(decision)
        # 决策解释推到消息栏
        self._msg(decision.explain)

    def last_decision(self) -> Optional[ArrangementDecision]:
        return self._last_decision

    # ── 决策 → 真实输出 ──────────────────────────────────────────────
    def _apply_decision(self, dec: ArrangementDecision):
        ad = self.adapters
        # matrix：当灯光主奏且项目有矩阵时，给矩阵一个随段落的模式（否则关）
        if ad.set_matrix and self.profile and self.profile.has_matrix:
            light = dec.subsystems.get(SUB_LIGHT)
            if light and light.role != ROLE_SILENT:
                mode = self._matrix_mode_for_section(dec.section, dec.energy)
                self._safe(ad.set_matrix, mode)
            else:
                self._safe(ad.set_matrix, None)

        for sub, sd in dec.subsystems.items():
            # solo 标志
            if ad.set_solo:
                self._safe(ad.set_solo, sub, bool(sd.solo_path))
            if sd.role == ROLE_SILENT:
                self._silence(sub)
            else:
                if ad.apply_subsystem:
                    self._safe(ad.apply_subsystem, sub, sd.preset_id, sd.level,
                               list(sd.group_ids), sd.speed, sd.solo_path)

    def _matrix_mode_for_section(self, section: str, energy: float) -> str:
        # MatrixPixelEngine 真实模式常量：高能段频谱/爆炸，低能段波纹/追逐
        if section in ("drop",):
            return "effect_explode"
        if section in ("chorus", "build"):
            return "audio_fft"
        if section in ("breakdown", "intro", "outro"):
            return "effect_ripple"
        return "audio_fft"

    def _silence(self, sub: str):
        if self.adapters.silence_subsystem:
            self._safe(self.adapters.silence_subsystem, sub)

    def _msg(self, text: str):
        if self.adapters.message:
            self._safe(self.adapters.message, text)

    @staticmethod
    def _safe(fn, *a):
        try:
            fn(*a)
        except Exception:
            pass
