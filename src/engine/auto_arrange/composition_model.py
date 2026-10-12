"""
composition_model — 学院派交响乐创作模型

把古典/交响乐创作的精细法则编码成可执行的决策层，让"自动编曲托管"像真正的
作曲家一样工作，而不是按段落贴标签。六大支柱：

  1. 曲式 Form          —— 呈示/展开/再现；再现部复用首次动机 → 结构整体性
  2. 配器 Orchestration —— 子系统映射乐器声部 + 织体(单声/主调/复调/全奏)
  3. 声部进行 VoiceLeading—— 多声部运动关系(反向/斜向/平行)，避免视觉单调
  4. 主题动机 Motif      —— 空间动机的陈述与变形(倒影/逆行/扩大/模进)
  5. 终止式 Cadence      —— 乐句结尾的渐慢+收束手势
  6. 力度 Dynamics       —— 乐句级渐强渐弱弧线

纯 core，无 UI/引擎依赖。被 arrangement_director 调用以生成精细决策。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

from .project_profile import ProjectProfile, GroupProfile, CAP_MOTOR, CAP_PUMP, CAP_VALVE, CAP_RGB
from .experience_table import SUB_LIGHT, SUB_PUMP, SUB_MOTOR, SUB_VALVE, SUBSYSTEMS


# ── 配器：管弦乐声部角色 ────────────────────────────────────────────────
class OrchestralRole(str, Enum):
    MELODY = "melody"          # 旋律声部（独奏/第一小提琴）——跟音高/精准动作
    FOUNDATION = "foundation"  # 基础声部（弦乐/低音）——持续铺底、宽覆盖
    HARMONY = "harmony"        # 和声声部（木管）——中层支撑、色彩
    PERCUSSION = "percussion"  # 节奏声部（打击乐）——强拍重音
    SILENT = "silent"          # 休止


# ── 织体类型 ────────────────────────────────────────────────────────────
class Texture(str, Enum):
    MONOPHONY = "monophony"    # 单声部：仅一个旋律声（独白/SOLO）
    HOMOPHONY = "homophony"    # 主调：旋律 + 和声/基础伴奏
    POLYPHONY = "polyphony"    # 复调：2-3 个独立声部对位
    TUTTI = "tutti"            # 全奏：所有声部齐鸣


# ── 声部进行：运动关系 ──────────────────────────────────────────────────
class MotionType(str, Enum):
    CONTRARY = "contrary"      # 反向：一扩一收（视觉对比最强）
    OBLIQUE = "oblique"        # 斜向：一动一保持
    PARALLEL = "parallel"      # 平行：同向（齐整，但多用单调）
    STATIC = "static"          # 静止/保持


# ── 曲式功能 ────────────────────────────────────────────────────────────
class FormFunction(str, Enum):
    INTRO = "intro"
    EXPOSITION = "exposition"      # 呈示：主题首次陈述
    DEVELOPMENT = "development"    # 展开：动机变形
    RECAPITULATION = "recap"       # 再现：主题回归（复用呈示）
    CODA = "coda"                  # 尾声


# ── 空间动机（可被变形）──────────────────────────────────────────────────
@dataclass
class SpatialMotif:
    """一个空间运动动机：几何取向 + 方向 + 相位铺开模式。"""
    geometry: str = "radial"       # radial(放射)/rotate(旋转)/sweep(扫掠)/pulse(脉冲)
    direction: int = 1             # +1/-1（用于倒影/逆行）
    phase_spread: float = 0.0      # 相位铺开 0..1
    speed: float = 1.0

    def invert(self) -> "SpatialMotif":
        """倒影：方向取反（上行↔下行、扩张↔收缩）。"""
        return SpatialMotif(self.geometry, -self.direction, self.phase_spread, self.speed)

    def retrograde(self) -> "SpatialMotif":
        """逆行：相位反向铺开。"""
        return SpatialMotif(self.geometry, self.direction, -self.phase_spread, self.speed)

    def augment(self, factor: float = 0.5) -> "SpatialMotif":
        """扩大：放慢（时值加长）。"""
        return SpatialMotif(self.geometry, self.direction, self.phase_spread, self.speed * factor)

    def diminish(self, factor: float = 2.0) -> "SpatialMotif":
        """缩小：加快。"""
        return SpatialMotif(self.geometry, self.direction, self.phase_spread, self.speed * factor)

    def to_dict(self) -> dict:
        return {"geometry": self.geometry, "direction": self.direction,
                "phase_spread": round(self.phase_spread, 2), "speed": round(self.speed, 2)}


# ── 单声部作曲决策 ──────────────────────────────────────────────────────
@dataclass
class VoiceComposition:
    subsystem: str = ""
    role: OrchestralRole = OrchestralRole.SILENT
    motion: MotionType = MotionType.STATIC
    motif: Optional[SpatialMotif] = None
    dynamic: float = 0.0           # 该声部当前力度 0..1
    note: str = ""

    def to_dict(self) -> dict:
        return {"subsystem": self.subsystem, "role": self.role.value, "motion": self.motion.value,
                "motif": self.motif.to_dict() if self.motif else None,
                "dynamic": round(self.dynamic, 2), "note": self.note}


@dataclass
class CompositionPlan:
    form: FormFunction = FormFunction.EXPOSITION
    texture: Texture = Texture.HOMOPHONY
    voices: Dict[str, VoiceComposition] = field(default_factory=dict)
    is_cadence: bool = False        # 是否处于终止式（乐句收束）
    ritardando: float = 1.0         # 速度系数（<1=渐慢，终止时）
    explain: str = ""

    def to_dict(self) -> dict:
        return {"form": self.form.value, "texture": self.texture.value,
                "is_cadence": self.is_cadence, "ritardando": round(self.ritardando, 2),
                "voices": {k: v.to_dict() for k, v in self.voices.items()}, "explain": self.explain}


# ── 配器表：子系统 → 该声部能扮演的管弦角色（按能力）──────────────────────
# 喷泉乐器学：电机=独奏旋律、泵=旋律/和声(表现性)、灯=基础/和声(铺底)、阀=打击
_ROLE_AFFINITY: Dict[str, List[OrchestralRole]] = {
    SUB_MOTOR: [OrchestralRole.MELODY, OrchestralRole.HARMONY],
    SUB_PUMP: [OrchestralRole.MELODY, OrchestralRole.HARMONY, OrchestralRole.FOUNDATION],
    SUB_LIGHT: [OrchestralRole.FOUNDATION, OrchestralRole.HARMONY],
    SUB_VALVE: [OrchestralRole.PERCUSSION],
}

_SUB_CN = {SUB_LIGHT: "灯光", SUB_PUMP: "水泵", SUB_MOTOR: "电机", SUB_VALVE: "阀"}
_ROLE_CN = {OrchestralRole.MELODY: "旋律", OrchestralRole.FOUNDATION: "基础",
            OrchestralRole.HARMONY: "和声", OrchestralRole.PERCUSSION: "节奏",
            OrchestralRole.SILENT: "休止"}
_TEX_CN = {Texture.MONOPHONY: "单声部", Texture.HOMOPHONY: "主调", Texture.POLYPHONY: "复调", Texture.TUTTI: "全奏"}
_MOTION_CN = {MotionType.CONTRARY: "反向", MotionType.OBLIQUE: "斜向", MotionType.PARALLEL: "平行", MotionType.STATIC: "保持"}


class CompositionState:
    """跨段落记忆：曲式主题库 + 已见段落，用于再现部复用。"""

    def __init__(self):
        self.theme_memory: Dict[str, CompositionPlan] = {}   # section_label → 首次方案
        self.seen_sections: List[str] = []
        self.section_occurrence: Dict[str, int] = {}         # 段落出现次数

    def reset(self):
        self.theme_memory.clear()
        self.seen_sections.clear()
        self.section_occurrence.clear()


class SymphonicComposer:
    """学院派交响乐作曲器：把项目画像 + 音乐上下文编织成精细作曲方案。"""

    def __init__(self, profile: ProjectProfile):
        self.profile = profile
        self.state = CompositionState()
        # 项目里真实存在的子系统
        caps = profile.cap_counts
        self._avail = [s for s, cap in (
            (SUB_LIGHT, CAP_RGB), (SUB_PUMP, CAP_PUMP),
            (SUB_MOTOR, CAP_MOTOR), (SUB_VALVE, CAP_VALVE)) if caps.get(cap)]

    # ── 曲式：判定当前段落的曲式功能 ───────────────────────────────────
    def _form_function(self, section: str) -> FormFunction:
        occ = self.state.section_occurrence.get(section, 0)
        if section in ("intro",):
            return FormFunction.INTRO
        if section in ("outro",):
            return FormFunction.CODA
        if section in ("build", "breakdown"):
            return FormFunction.DEVELOPMENT
        # chorus/drop/verse 首次=呈示，再次=再现
        return FormFunction.RECAPITULATION if occ >= 1 else FormFunction.EXPOSITION

    # ── 织体：能量 + 可用声部数 → 织体密度 ─────────────────────────────
    def _texture(self, energy: float, is_solo: bool) -> Texture:
        if is_solo:
            return Texture.MONOPHONY
        n = len(self._avail)
        if energy >= 0.85 and n >= 3:
            return Texture.TUTTI
        if energy >= 0.55:
            return Texture.POLYPHONY
        return Texture.HOMOPHONY

    # ── 配器：把可用声部分配管弦角色 ───────────────────────────────────
    def _orchestrate(self, texture: Texture, energy: float,
                     solo_target: str) -> Dict[str, OrchestralRole]:
        roles: Dict[str, OrchestralRole] = {s: OrchestralRole.SILENT for s in self._avail}

        def assign(sub: str, role: OrchestralRole) -> bool:
            if sub in roles and role in _ROLE_AFFINITY.get(sub, []):
                roles[sub] = role
                return True
            return False

        # 1) 选旋律声部（独奏家）：solo_target 优先，否则电机>泵
        melody_order = ([solo_target] if solo_target else []) + [SUB_MOTOR, SUB_PUMP, SUB_LIGHT]
        melody_sub = None
        for s in melody_order:
            if s in self._avail and OrchestralRole.MELODY in _ROLE_AFFINITY.get(s, []):
                roles[s] = OrchestralRole.MELODY
                melody_sub = s
                break

        if texture == Texture.MONOPHONY:
            return roles  # 仅旋律，其余休止

        # 2) 基础声部：灯光优先（弦乐铺底），否则泵
        for s in (SUB_LIGHT, SUB_PUMP):
            if s in self._avail and s != melody_sub and roles[s] == OrchestralRole.SILENT:
                if assign(s, OrchestralRole.FOUNDATION):
                    break

        if texture in (Texture.POLYPHONY, Texture.TUTTI):
            # 3) 和声声部：剩余 motor/pump
            for s in (SUB_PUMP, SUB_MOTOR, SUB_LIGHT):
                if s in self._avail and roles[s] == OrchestralRole.SILENT:
                    if assign(s, OrchestralRole.HARMONY):
                        break
            # 4) 节奏声部：阀（打击乐），高能量才进
            if energy >= 0.6 and SUB_VALVE in self._avail and roles[SUB_VALVE] == OrchestralRole.SILENT:
                assign(SUB_VALVE, OrchestralRole.PERCUSSION)

        if texture == Texture.TUTTI:
            # 全奏：把还休止的可用声部尽量编入（doubling 加倍）
            for s in self._avail:
                if roles[s] == OrchestralRole.SILENT:
                    aff = _ROLE_AFFINITY.get(s, [])
                    if aff:
                        roles[s] = aff[0]
        return roles

    # ── 声部进行：给共同活跃的声部分配运动关系 ─────────────────────────
    def _voice_leading(self, roles: Dict[str, OrchestralRole]) -> Dict[str, MotionType]:
        active = [s for s, r in roles.items() if r != OrchestralRole.SILENT]
        motion: Dict[str, MotionType] = {}
        melody = [s for s, r in roles.items() if r == OrchestralRole.MELODY]
        foundation = [s for s, r in roles.items() if r == OrchestralRole.FOUNDATION]
        for s in active:
            r = roles[s]
            if r == OrchestralRole.MELODY:
                motion[s] = MotionType.OBLIQUE     # 旋律自由movement
            elif r == OrchestralRole.FOUNDATION:
                # 基础与旋律反向进行（一扩一收）——视觉对比
                motion[s] = MotionType.CONTRARY if melody else MotionType.OBLIQUE
            elif r == OrchestralRole.HARMONY:
                # 和声与基础斜向，避免与谁都平行
                motion[s] = MotionType.OBLIQUE
            else:  # PERCUSSION
                motion[s] = MotionType.STATIC      # 打击乐定点重音
        return motion

    # ── 主题动机：呈示陈述 / 再现复用 / 展开变形 ───────────────────────
    def _motif_for(self, sub: str, role: OrchestralRole, form: FormFunction,
                   energy: float) -> SpatialMotif:
        base = SpatialMotif(
            geometry={OrchestralRole.MELODY: "rotate", OrchestralRole.FOUNDATION: "radial",
                      OrchestralRole.HARMONY: "sweep", OrchestralRole.PERCUSSION: "pulse"}.get(role, "radial"),
            direction=1,
            phase_spread=0.6 if role in (OrchestralRole.FOUNDATION, OrchestralRole.HARMONY) else 0.3,
            speed=0.6 + energy * 1.0,
        )
        if form == FormFunction.DEVELOPMENT:
            # 展开部：动机变形（倒影 / 缩小加密）
            return base.invert().diminish(1.3)
        if form == FormFunction.CODA:
            return base.augment(0.5)  # 尾声扩大放慢
        return base

    # ── 终止式 + 力度弧线 ──────────────────────────────────────────────
    def _cadence_and_dynamics(self, phrase_progress: float, energy: float
                              ) -> Tuple[bool, float, float]:
        """返回 (is_cadence, ritardando速度系数, 力度基线)。
        phrase_progress 0..1：乐句内进度，接近1=乐句末→终止式渐慢。"""
        is_cadence = phrase_progress >= 0.85
        ritardando = 1.0
        if is_cadence:
            # 终止处渐慢到 0.7
            ritardando = 1.0 - 0.3 * ((phrase_progress - 0.85) / 0.15)
        # 力度弧线：乐句前半渐强、后半微弱（拱形），叠加段落能量
        arc = (phrase_progress * 2.0) if phrase_progress < 0.5 else (2.0 - phrase_progress * 2.0)
        dyn = max(0.0, min(1.0, 0.5 * energy + 0.5 * (0.4 + 0.6 * arc) * energy))
        return is_cadence, ritardando, dyn

    # ── 主入口 ──────────────────────────────────────────────────────────
    def compose(self, section: str, energy: float, is_solo: bool,
                solo_target: str = "", phrase_progress: float = 0.5) -> CompositionPlan:
        # 记录段落出现次数（先查后记，本次决定曲式功能用旧值）
        form = self._form_function(section)
        self.state.section_occurrence[section] = self.state.section_occurrence.get(section, 0) + 1
        self.state.seen_sections.append(section)

        # 再现部：若该段落首次方案已存档，复用其配器/动机（主题回归）
        if form == FormFunction.RECAPITULATION and section in self.state.theme_memory:
            base_plan = self.state.theme_memory[section]
            plan = self._intensify(base_plan, energy)
            plan.form = FormFunction.RECAPITULATION
            is_cad, rit, _ = self._cadence_and_dynamics(phrase_progress, energy)
            plan.is_cadence = is_cad
            plan.ritardando = rit
            plan.explain = self._explain(plan, recap=True)
            return plan

        texture = self._texture(energy, is_solo)
        roles = self._orchestrate(texture, energy, solo_target)
        motion = self._voice_leading(roles)
        is_cad, rit, dyn = self._cadence_and_dynamics(phrase_progress, energy)

        plan = CompositionPlan(form=form, texture=texture, is_cadence=is_cad, ritardando=rit)
        for sub in self._avail:
            role = roles.get(sub, OrchestralRole.SILENT)
            vc = VoiceComposition(subsystem=sub, role=role)
            if role != OrchestralRole.SILENT:
                vc.motion = motion.get(sub, MotionType.OBLIQUE)
                vc.motif = self._motif_for(sub, role, form, energy)
                vc.motif.speed *= rit  # 终止式渐慢作用到动机速度
                # 旋律声部力度最满，基础次之
                role_gain = {OrchestralRole.MELODY: 1.0, OrchestralRole.FOUNDATION: 0.7,
                             OrchestralRole.HARMONY: 0.6, OrchestralRole.PERCUSSION: 0.85}.get(role, 0.5)
                vc.dynamic = max(0.0, min(1.0, dyn * role_gain))
                vc.note = f"{_SUB_CN.get(sub,sub)}={_ROLE_CN[role]}/{_MOTION_CN[vc.motion]}"
            plan.voices[sub] = vc

        # 呈示部存档为主题，供再现复用
        if form == FormFunction.EXPOSITION:
            self.state.theme_memory[section] = plan
        plan.explain = self._explain(plan)
        return plan

    def _intensify(self, base: CompositionPlan, energy: float) -> CompositionPlan:
        """再现部：复用主题但按当前能量强化（力度提升、可能升织体）。"""
        new = CompositionPlan(form=base.form, texture=base.texture)
        for sub, vc in base.voices.items():
            nv = VoiceComposition(subsystem=sub, role=vc.role, motion=vc.motion,
                                  motif=vc.motif, note=(vc.note + "·再现"))
            nv.dynamic = max(vc.dynamic, min(1.0, vc.dynamic * 1.15 + 0.1 * energy))
            new.voices[sub] = nv
        return new

    def _explain(self, plan: CompositionPlan, recap: bool = False) -> str:
        form_cn = {FormFunction.INTRO: "引子", FormFunction.EXPOSITION: "呈示",
                   FormFunction.DEVELOPMENT: "展开", FormFunction.RECAPITULATION: "再现",
                   FormFunction.CODA: "尾声"}.get(plan.form, plan.form.value)
        voices = []
        for sub, vc in plan.voices.items():
            if vc.role != OrchestralRole.SILENT:
                voices.append(f"{_SUB_CN.get(sub,sub)}({_ROLE_CN[vc.role]}·{_MOTION_CN[vc.motion]})")
        parts = [f"【{form_cn}部·{_TEX_CN[plan.texture]}】"]
        if voices:
            parts.append("配器:" + "+".join(voices))
        if plan.is_cadence:
            parts.append(f"终止式(渐慢{plan.ritardando:.2f})")
        if recap:
            parts.append("主题回归")
        return " ".join(parts)
