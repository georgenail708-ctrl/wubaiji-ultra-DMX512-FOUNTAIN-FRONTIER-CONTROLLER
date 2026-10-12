"""
arrangement_director — 编曲总指挥

托管的大脑。输入 = 项目画像 + 当前音乐上下文（段落/能量/是否solo/BPM）。
输出 = 本段编排决策：每个子系统的角色(主奏/陪衬/留白) + 选哪些设备组 + 用什么 preset/参数
       + 一句人类可读的决策解释（给实时消息栏）。

核心哲学（用户强调）：真实编曲不是给每类设备都塞 effect，而是子系统轮流主奏，
其余陪衬或留白。本模块按段落+音乐+项目画像做这个"谁主奏/谁陪衬/谁闭嘴"的分配。

纯 core，无 UI / 引擎依赖。决策结果由上层（引擎/面板）翻译成实际输出。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from .project_profile import (
    ProjectProfile, GroupProfile,
    CAP_MOTOR, CAP_PUMP, CAP_VALVE, CAP_RGB, CAP_DIMMER,
)
from . import experience_table as EXP
from .experience_table import (
    SUB_LIGHT, SUB_PUMP, SUB_MOTOR, SUB_VALVE, SUBSYSTEMS,
    ROLE_LEAD, ROLE_SUPPORT, ROLE_SILENT, SUPPORT_LEVEL,
)
def _get_feature_names() -> list:
    """Lazily fetch FEATURE_NAMES from arrangement_learner.

    Importing arrangement_learner eagerly drags in sklearn (~1.1s), which
    froze the Audio Bridge panel on first open. FEATURE_NAMES is only needed
    on the ML-enhanced explain path, so defer the import until then.
    """
    try:
        from .arrangement_learner import FEATURE_NAMES
        return FEATURE_NAMES
    except ImportError:
        return []


# 子系统 → 该子系统需要的设备能力
_SUB_CAP = {
    SUB_LIGHT: CAP_RGB,
    SUB_PUMP: CAP_PUMP,
    SUB_MOTOR: CAP_MOTOR,
    SUB_VALVE: CAP_VALVE,
}

_SUB_CN = {SUB_LIGHT: "灯光", SUB_PUMP: "水泵", SUB_MOTOR: "电机", SUB_VALVE: "阀"}
_ROLE_CN = {ROLE_LEAD: "主奏", ROLE_SUPPORT: "陪衬", ROLE_SILENT: "留白"}


@dataclass
class SubsystemDecision:
    subsystem: str = ""
    role: str = ROLE_SILENT
    level: float = 0.0             # 输出强度系数 0..1（主奏~1，陪衬~0.35，留白0）
    group_ids: List[str] = field(default_factory=list)   # 选中设备的成员 fixture id（已由组展开）
    preset_id: str = ""           # 用哪个 preset（主奏/陪衬时）
    solo_path: bool = False       # 是否走 solo 路径（电机路径/泵跟旋律）
    speed: float = 1.0
    note: str = ""

    def to_dict(self) -> dict:
        return {"subsystem": self.subsystem, "role": self.role, "level": round(self.level, 2),
                "group_ids": list(self.group_ids), "preset_id": self.preset_id,
                "solo_path": self.solo_path, "speed": round(self.speed, 2), "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "SubsystemDecision":
        d = d or {}
        return cls(
            subsystem=str(d.get("subsystem", "")),
            role=str(d.get("role", ROLE_SILENT)),
            level=float(d.get("level", 0.0) or 0.0),
            group_ids=[str(x) for x in (d.get("group_ids", []) or [])],
            preset_id=str(d.get("preset_id", "")),
            solo_path=bool(d.get("solo_path", False)),
            speed=float(d.get("speed", 1.0) or 1.0),
            note=str(d.get("note", "")),
        )


@dataclass
class ArrangementDecision:
    section: str = ""
    energy: float = 0.5
    subsystems: Dict[str, SubsystemDecision] = field(default_factory=dict)
    explain: str = ""             # 给实时消息栏的一句话解释

    def to_dict(self) -> dict:
        return {"section": self.section, "energy": round(self.energy, 2),
                "subsystems": {k: v.to_dict() for k, v in self.subsystems.items()},
                "explain": self.explain}

    @classmethod
    def from_dict(cls, d: dict) -> "ArrangementDecision":
        d = d or {}
        subs = {}
        for k, v in (d.get("subsystems", {}) or {}).items():
            subs[str(k)] = SubsystemDecision.from_dict(v)
        return cls(
            section=str(d.get("section", "")),
            energy=float(d.get("energy", 0.5) or 0.5),
            subsystems=subs,
            explain=str(d.get("explain", "")),
        )


@dataclass
class MusicCue:
    """喂给总指挥的音乐上下文（从音频分析/识别取）。"""
    section: str = "verse"
    energy: float = 0.5
    is_solo: bool = False
    solo_target: str = ""         # solo 指向的子系统（如 motor/pump），空=自动按段落
    bpm: float = 120.0
    has_audio: bool = False
    phrase_progress: float = 0.5  # 乐句内进度 0..1（接近1=乐句末→终止式渐慢）


class ArrangementDirector:
    """编曲总指挥。"""

    def __init__(self, profile: ProjectProfile,
                 learned_presets: Optional[Dict[str, Dict[str, List[str]]]] = None,
                 symphonic: bool = True, seed: int = 0,
                 ml_manager=None, bandit=None):
        self.profile = profile
        # 经验：内置默认 + 可选从项目学到的偏好（学到的优先）
        self.learned = learned_presets or {}
        # ★ 随机种子：用于"重新编排"产生不同变体（在候选 preset 池里换选择）。
        #   seed=0（默认）保持原确定性行为（恒取候选[0]），逐字节不变。
        self.seed = int(seed)
        self._rng = None
        if self.seed:
            import random as _random
            self._rng = _random.Random(self.seed)
        # ML 模型管理器（可选 — 未训练时为 None，静默回退规则引擎）
        self.ml_manager = ml_manager
        # K-摇臂赌博机（RL 在线学习 — 从用户反馈中学习 preset/role 偏好）
        self._bandit = bandit
        # 预先把"哪些组属于哪个子系统"索引好
        self._sub_groups: Dict[str, List[GroupProfile]] = {s: [] for s in SUBSYSTEMS}
        self._index_groups()
        # 学院派交响乐作曲器（曲式/配器/声部进行/动机/终止式/力度）
        self.symphonic = bool(symphonic)
        self._composer = None
        if self.symphonic:
            try:
                from .composition_model import SymphonicComposer
                self._composer = SymphonicComposer(profile)
            except Exception:
                self._composer = None
                self.symphonic = False
        # 编排级引擎（同心环分层 + 声部对话 + 卡农入场）
        self._arranger_v2 = None
        try:
            from .arranger_v2 import ArrangerV2
            self._arranger_v2 = ArrangerV2(profile, seed=self.seed)
        except ImportError:
            pass

    def _index_groups(self):
        for g in self.profile.groups:
            for sub, cap in _SUB_CAP.items():
                if cap in g.caps:
                    self._sub_groups[sub].append(g)

    def _available_subs(self) -> List[str]:
        """项目里真实存在设备的子系统。"""
        caps = self.profile.cap_counts
        out = []
        if caps.get(CAP_RGB) or caps.get(CAP_DIMMER):
            out.append(SUB_LIGHT)
        if caps.get(CAP_PUMP):
            out.append(SUB_PUMP)
        if caps.get(CAP_MOTOR):
            out.append(SUB_MOTOR)
        if caps.get(CAP_VALVE):
            out.append(SUB_VALVE)
        return out

    def _pick_preset(self, section: str, sub: str) -> str:
        # 学到的优先，否则内置经验
        cand = (self.learned.get(section, {}).get(sub)
                or EXP.get_preset_candidates(section, sub))
        if not cand:
            return ""
        # ★ RL 增强：用 bandit 在候选中选最佳
        if self._bandit is not None and len(cand) > 1:
            # 构造 (role, preset) 候选对
            pairs = []
            for p in cand:
                pairs.append((ROLE_LEAD, p))
                pairs.append((ROLE_SUPPORT, p))
            best = self._bandit.best_arm(section, sub, pairs)
            if best is not None:
                return best[1]  # (role, preset) → preset
        # 回退：带种子时在候选池里随机挑；无种子恒取[0]（确定性）。
        if self._rng is not None and len(cand) > 1:
            return self._rng.choice(cand)
        return cand[0]

    @staticmethod
    def _members_of(groups: List[GroupProfile]) -> List[str]:
        """把选中的组展开成去重后的成员 fixture id 列表。

        ★ 关键修复：下游 effect 引擎按 fixture **实例 id** 反查设备，过去这里
        返回的是组 id（group_id），effect 层 _fixtures_from_ids 全部 miss → 整条
        类型被跳过 → 灯/泵/电机/阀完全无 DMX 输出。改为展开 member_ids 后打通。
        """
        out: List[str] = []
        seen = set()
        for g in groups:
            for mid in (g.member_ids or []):
                sid = str(mid)
                if sid and sid not in seen:
                    out.append(sid)
                    seen.add(sid)
        return out

    def _pick_groups_for_lead(self, sub: str) -> List[str]:
        """主奏选组：优先选"环/圆"几何的组（喷泉演出最出彩），按规模适中排序。
        返回选中组的成员 fixture id（非组 id）。"""
        groups = list(self._sub_groups.get(sub, []))
        if not groups:
            return []
        # 环形组优先；规模适中（避免全场齐动失去主奏感）
        def score(g: GroupProfile):
            ring_bonus = 2.0 if g.shape == "ring" else (1.0 if g.shape == "line" else 0.0)
            # 中心环更适合做主奏焦点
            center_bonus = 1.0 if "center" in g.semantic_tags else 0.0
            # 规模惩罚（太大不像 solo）
            size_pen = -0.001 * max(0, g.count - 36)
            return ring_bonus + center_bonus + size_pen
        groups.sort(key=score, reverse=True)
        # 主奏取 1~2 个最出彩的组，展开成成员 fixture id
        return self._members_of([g for g in groups[:2] if g.group_id])

    def _pick_groups_for_support(self, sub: str) -> List[str]:
        """陪衬选组：用大面积/散布组铺底。返回选中组的成员 fixture id（非组 id）。"""
        groups = list(self._sub_groups.get(sub, []))
        groups.sort(key=lambda g: g.count, reverse=True)
        return self._members_of([g for g in groups[:1] if g.group_id])

    def decide(self, cue: MusicCue) -> ArrangementDecision:
        """核心决策：段落+音乐+画像 → 各子系统角色分配。"""
        section = cue.section if cue.section in EXP.SECTIONS else "verse"

        # ── 编排级引擎优先（ArrangerV2）──
        # 同心环分层 + 声部对话 + 卡农入场 — 对标标杆 81 clip
        if self._arranger_v2 is not None:
            try:
                dec = self._decide_with_arranger_v2(cue, section)
                return self._ml_blend(dec, cue)
            except Exception:
                pass  # 失败回退

        # 交响乐模式：走学院派作曲器
        if self.symphonic and self._composer is not None:
            try:
                dec = self._decide_symphonic(cue, section)
                return self._ml_blend(dec, cue)
            except Exception:
                pass
        plan = EXP.get_section_plan(section)
        avail = self._available_subs()

        dec = ArrangementDecision(section=section, energy=cue.energy or plan.energy)
        lead_subs = []
        support_subs = []
        silent_subs = []

        # 1) 基线角色来自段落计划，但只对"项目真有的子系统"生效
        for sub in SUBSYSTEMS:
            if sub not in avail:
                continue
            role = plan.roles.get(sub, ROLE_SILENT)
            # 若该角色需要的组在项目里不存在，降级
            if role != ROLE_SILENT and not self._sub_groups.get(sub):
                role = ROLE_SILENT
            if role == ROLE_LEAD:
                lead_subs.append(sub)
            elif role == ROLE_SUPPORT:
                support_subs.append(sub)
            else:
                silent_subs.append(sub)

        # 2) solo 模式（C5 真实音高判定）覆盖：把主奏收敛到 solo_target，其余降为陪衬/留白
        if cue.is_solo:
            target = cue.solo_target
            if not target or target not in avail:
                # 按段落偏好选一个可用的主奏
                for s in plan.lead_pref:
                    if s in avail and self._sub_groups.get(s):
                        target = s
                        break
            if target:
                lead_subs = [target]
                support_subs = [s for s in avail if s != target and s in (support_subs + lead_subs)]
                # solo 段其余压低/留白
                silent_subs = [s for s in avail if s not in lead_subs and s not in support_subs]

        # 3) 留白裁剪：若该段不允许留白，把 silent 提升为 support
        if not plan.allow_silence:
            for s in list(silent_subs):
                if self._sub_groups.get(s):
                    support_subs.append(s)
                    silent_subs.remove(s)

        # 4) 生成每个子系统的决策
        for sub in avail:
            d = SubsystemDecision(subsystem=sub)
            d.speed = plan.speed_scale
            if sub in lead_subs:
                d.role = ROLE_LEAD
                d.level = 1.0
                d.group_ids = self._pick_groups_for_lead(sub)
                d.preset_id = self._pick_preset(section, sub)
                # 电机/泵主奏且 solo → 走路径/旋律 solo
                if cue.is_solo and sub in (SUB_MOTOR, SUB_PUMP):
                    d.solo_path = True
                d.note = f"{_SUB_CN[sub]}主奏"
                if d.solo_path:
                    d.note += "(走solo路径)"
            elif sub in support_subs:
                d.role = ROLE_SUPPORT
                d.level = SUPPORT_LEVEL
                d.group_ids = self._pick_groups_for_support(sub)
                d.preset_id = self._pick_preset(section, sub)
                d.speed = plan.speed_scale * 0.8
                d.note = f"{_SUB_CN[sub]}陪衬铺底"
            else:
                d.role = ROLE_SILENT
                d.level = 0.0
                d.note = f"{_SUB_CN[sub]}留白"
            dec.subsystems[sub] = d

        dec.explain = self._explain(dec, plan, cue)
        # ── 喷泉编排引擎增强 ────────────────────────────
        self._apply_fountain_enhancement(dec, section, cue)
        # ── ML 增强 ─────────────────────────────────────
        return self._ml_blend(dec, cue)

    def _decide_symphonic(self, cue: MusicCue, section: str) -> ArrangementDecision:
        """学院派交响乐路径：作曲器输出 CompositionPlan → 映射为各子系统决策。"""
        from .composition_model import OrchestralRole
        plan = self._composer.compose(
            section=section, energy=cue.energy, is_solo=cue.is_solo,
            solo_target=cue.solo_target, phrase_progress=cue.phrase_progress)
        dec = ArrangementDecision(section=section, energy=cue.energy)
        # 管弦角色 → 主奏/陪衬等级
        lead_roles = (OrchestralRole.PRIMARY_MELODY, OrchestralRole.COUNTER_MELODY) \
            if hasattr(OrchestralRole, "PRIMARY_MELODY") else (OrchestralRole.MELODY,)
        for sub, vc in plan.voices.items():
            d = SubsystemDecision(subsystem=sub)
            role = vc.role
            # 速度：动机速度 × 终止式渐慢
            motif_speed = vc.motif.speed if vc.motif else 1.0
            d.speed = max(0.1, motif_speed)
            if role.value == "silent":
                d.role = ROLE_SILENT
                d.level = 0.0
                d.note = f"{_SUB_CN.get(sub,sub)}休止"
            else:
                # 旋律=主奏；其余=陪衬（按力度分级）
                is_melody = role.value in ("melody", "primary_melody", "counter_melody")
                d.role = ROLE_LEAD if is_melody else ROLE_SUPPORT
                d.level = max(0.0, min(1.0, vc.dynamic))
                if d.role == ROLE_LEAD:
                    d.group_ids = self._pick_groups_for_lead(sub)
                    # 电机/泵旋律 + solo → 走路径
                    if cue.is_solo and sub in (SUB_MOTOR, SUB_PUMP):
                        d.solo_path = True
                    elif role.value in ("melody", "primary_melody") and sub in (SUB_MOTOR, SUB_PUMP):
                        d.solo_path = True
                else:
                    d.group_ids = self._pick_groups_for_support(sub)
                d.preset_id = self._pick_preset(section, sub)
                # 动机相位铺开/方向写进 preset 参数（供 effect 引擎 phase_spread）
                if vc.motif:
                    d.note = f"{_SUB_CN.get(sub,sub)}·{vc.note}"
                else:
                    d.note = _SUB_CN.get(sub, sub)
            dec.subsystems[sub] = d
        # 决策解释直接用作曲器的（含曲式/配器/终止式），更专业
        dec.explain = plan.explain
        return dec

    def _decide_with_arranger_v2(self, cue: MusicCue, section: str) -> ArrangementDecision:
        """编排级决策：用 ArrangerV2 的同心环分层模型替代旧的经验法。

        对标标杆 81 clip 的编排粒度 — 每种设备类型×每个环×每种用法 = 独立声部。
        """
        # 从 cue 提取音乐特征（兜底默认值）
        energy = cue.energy or 0.5
        percussive = getattr(cue, 'percussive', 0.5)
        melodic = getattr(cue, 'melodic', 0.1)

        parts = self._arranger_v2.select_parts(
            energy=energy, percussive=percussive, melodic=melodic,
            section_duration_s=8.0,
            section_idx=getattr(cue, 'section_idx', 0),
            _n_sections=max(1, getattr(cue, 'n_sections', 1)),
        )

        dec = ArrangementDecision(section=section, energy=energy)

        # 将 ArrangerV2 的 Part 列表翻译为 ArrangementDecision 的子系统决策
        # Part: (device_type, usage, group, topology, speed, color)
        # SubsystemDecision: (subsystem, role, level, group_ids, preset_id, speed, note)

        # 按 subsystem 聚合 parts
        subs_parts: Dict[str, List] = {}
        for part in parts:
            # 映射 device_type+usage → subsystem
            sub = self._map_part_to_subsystem(part)
            if sub:
                subs_parts.setdefault(sub, []).append(part)

        for sub, sub_parts in subs_parts.items():
            if not sub_parts:
                continue

            # 该子系统在此段是 lead/support/silent
            # 规则: 有 seq 或 motor_transition 的 = lead, 其余 = support
            has_lead = any(p.sequential or p.motor_transition for p in sub_parts)
            role = ROLE_LEAD if has_lead else ROLE_SUPPORT

            # 收集该子系统所有 part 的 fixture IDs
            all_members = []
            for p in sub_parts:
                all_members.extend(p.group_ids)

            # 取第一个 part 的速度和 preset 参考
            first = sub_parts[0]
            speed = max(0.1, first.speed / 100.0) if first.speed > 0 else 1.0
            note_parts = []
            if first.sequential:
                note_parts.append(first.sequential)
            if first.color_mode and first.color_mode != "white":
                note_parts.append(first.color_mode)
            topology_counts = {}
            for p in sub_parts:
                topology_counts[p.topology] = topology_counts.get(p.topology, 0) + 1
            topo_str = "+".join(f"{v}{k}" for k, v in sorted(topology_counts.items(), key=lambda x: -x[1])[:2])

            d = SubsystemDecision(
                subsystem=sub,
                role=role,
                level=0.85 if role == ROLE_LEAD else SUPPORT_LEVEL,
                group_ids=list(set(all_members)),  # 去重
                speed=speed,
                note=f"{_SUB_CN.get(sub, sub)} · {topo_str} · {' '.join(note_parts)}" if (note_parts or topo_str) else _SUB_CN.get(sub, sub),
            )
            dec.subsystems[sub] = d

        # 补全未涉及的子系统为留白
        for sub in SUBSYSTEMS:
            if sub not in dec.subsystems and sub in self._available_subs():
                dec.subsystems[sub] = SubsystemDecision(
                    subsystem=sub, role=ROLE_SILENT, level=0.0,
                    note=f"{_SUB_CN.get(sub, sub)}留白",
                )

        dec.explain = f"[编排级] {len(parts)}声部 · {len(dec.subsystems)}子系统 · E={energy:.2f}"
        return dec

    @staticmethod
    def _map_part_to_subsystem(part) -> Optional[str]:
        """将 ArrangerV2 Part 的设备类型+用法映射到子系统。"""
        dt = part.device_type.lower()
        usage = part.usage
        # 气爆类 → valve
        if "airburst" in dt or ("valve" in dt and "pump" in dt):
            return SUB_VALVE
        # 泵
        if usage == "泵" or "pump" in dt:
            return SUB_PUMP
        # 数控/电机
        if usage == "数控" or "motor" in dt:
            return SUB_MOTOR
        # 阀
        if usage == "阀" or "valve" in dt:
            return SUB_VALVE
        # RGB/LED → light
        if "rgb" in usage.lower() or "led" in dt or "灯组" in dt:
            return SUB_LIGHT
        return None

    def _ml_blend(self, dec: ArrangementDecision, cue: MusicCue) -> ArrangementDecision:
        """ML 增强层：将 ML 预测融入规则引擎决策。

        融合策略:
          - motor_xyz: 完全由 PCA 映射决定（覆盖规则, 如果 ML 已训练）
          - role: ML 和规则都 predict → 取 ML 的（ML 权重可配）
          - level: 加权平均 0.3*rule + 0.7*ml
          - preset_id: 优先 ML 推荐（KMeans 模板），不在候选列表时用规则

        模型未训练时静默回退，直接返回原决策。
        """
        if self.ml_manager is None or not self.ml_manager.is_trained():
            return dec

        try:
            seg_dict = self._cue_to_segment_dict(cue)
            enhanced = self.ml_manager.predict(seg_dict, bpm=cue.bpm)
        except Exception:
            return dec

        if enhanced.confidence <= 0:
            return dec

        # ── Motor XYZ: 完全由 PCA 驱动 ──
        mx, my, mz = enhanced.motor_xyz
        if any(abs(v) > 0.001 for v in (mx, my, mz)):
            motor_dec = dec.subsystems.get(SUB_MOTOR)
            if motor_dec is not None and motor_dec.role != ROLE_SILENT:
                # 将 PCA 输出写入 note 供下游 effect 引擎读取
                motor_dec.note = (motor_dec.note or "") + f" [ML:xyz=({mx:.2f},{my:.2f},{mz:.2f})]"

        # ── Role: ML 覆盖（如果 ML 置信度高）──
        for sub, ml_role in enhanced.ml_role.items():
            if sub not in dec.subsystems:
                continue
            d = dec.subsystems[sub]
            # 仅在 ML 置信度 > 0.5 时覆盖
            if enhanced.confidence > 0.5 and ml_role != ROLE_SILENT:
                d.role = ml_role
                d.note = (d.note or "") + f" [ML角色:{ml_role}]"
                # ★ RL 反馈: 不在此自动记录 — bandit 应从用户手动调整后
                # 的真实反馈学习，而非从 ML 自身决策学习。

        # ── Level: 加权平均 ──
        ML_WEIGHT = 0.7
        for sub, ml_level in enhanced.ml_level.items():
            if sub not in dec.subsystems:
                continue
            d = dec.subsystems[sub]
            rule_level = d.level
            d.level = rule_level * (1.0 - ML_WEIGHT) + ml_level * ML_WEIGHT

        # ── Template: KMeans 推荐 preset（如果 sub 无 preset）──
        if enhanced.template_id >= 0:
            for sub, d in dec.subsystems.items():
                if d.role != ROLE_SILENT and not d.preset_id:
                    d.note = (d.note or "") + f" [模板#{enhanced.template_id}]"

        # 标记 ML 增强
        if enhanced.top_features:
            _feat_names = _get_feature_names()
            top_names = [_feat_names[i] for i in enhanced.top_features[:5]
                         if i < len(_feat_names)]
            dec.explain = (dec.explain or "") + f" · ML增强(置信度{enhanced.confidence:.0%}, "
            dec.explain += f"关键特征:{','.join(top_names)})" if top_names else ")"

        return dec

    @staticmethod
    def _cue_to_segment_dict(cue: MusicCue) -> dict:
        """将 MusicCue 转换为 predict() 所需的 segment 字典。

        MusicCue 缺少 t_start/t_end（实时决策没有绝对时间），
        使用合理的占位值。duration=8s 是典型音乐段的长度，
        对 duration_norm 特征 (feats[33]=duration/60) 影响很小。
        """
        return {
            "section": cue.section,
            "energy": cue.energy,
            "t_start": 0.0,
            "t_end": 8.0,         # 典型段长，对特征影响小
        }

    def _apply_fountain_enhancement(self, dec: ArrangementDecision, section: str, cue: MusicCue) -> None:
        """喷泉编排引擎增强：按设备性格覆盖速度/色彩值（不改角色分配）。

        对标 To The Moon 81 clip 实测数据, 为经验法/交响乐法补充设备级别的精细参数。
        """
        try:
            from .fountain_arranger import FountainArranger
            fount = FountainArranger.arrange(
                energy=cue.energy,
                percussive=getattr(cue, 'percussive', 0.5),
                melodic=getattr(cue, 'melodic', 0.1),
                bpm=cue.bpm,
                section_duration_s=8.0,
                is_first_section=(section in ('intro',)),
                is_last_section=(section in ('outro',)),
                rng_seed=self.seed,
            )
            _dev_map = {'light': 'LED灯组|RGB', 'pump': '泵', 'motor': '数控',
                        'valve': '阀', 'dimmer': '气爆'}
            for sub, d in dec.subsystems.items():
                if d.role == ROLE_SILENT:
                    continue
                dev_key = _dev_map.get(sub, '')
                if dev_key and dev_key in fount.device_speeds:
                    speed = fount.device_speeds[dev_key]
                    if speed > 0 and abs(d.speed - 1.0) < 0.02:
                        d.speed = max(0.1, speed / 100.0)
                    if dev_key in fount.device_colors:
                        color = fount.device_colors[dev_key]
                        if '·' not in (d.note or ''):
                            d.note = f"{d.note} · {color}" if d.note else color
        except ImportError:
            pass

    def _explain(self, dec: ArrangementDecision, plan, cue: MusicCue) -> str:
        leads =[_SUB_CN[s] for s, d in dec.subsystems.items() if d.role == ROLE_LEAD]
        sups = [_SUB_CN[s] for s, d in dec.subsystems.items() if d.role == ROLE_SUPPORT]
        sils = [_SUB_CN[s] for s, d in dec.subsystems.items() if d.role == ROLE_SILENT]
        seg_cn = {"intro": "前奏", "verse": "主歌", "build": "推进", "chorus": "副歌",
                  "drop": "高潮", "breakdown": "间奏", "outro": "尾声"}.get(dec.section, dec.section)
        parts = [f"【{seg_cn}】能量{dec.energy:.0%}"]
        if cue.is_solo:
            parts.append("旋律SOLO")
        if leads:
            solo_note = ""
            for s, d in dec.subsystems.items():
                if d.solo_path:
                    solo_note = "走路径"
            parts.append("主奏:" + "+".join(leads) + (f"({solo_note})" if solo_note else ""))
        if sups:
            parts.append("陪衬:" + "+".join(sups))
        if sils:
            parts.append("留白:" + "+".join(sils))
        return " · ".join(parts)
