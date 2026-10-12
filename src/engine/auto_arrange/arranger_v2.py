"""
arranger_v2.py — 编排级喷泉秀引擎

核心模型（对标 To The Moon 81 clip 的真实编排）：

  一个"声部"(Part) = (设备类型 × 用法 × 特定设备组 × 拓扑 × 空间位置)

  不是"泵开/关"这种粗粒度——同一设备类型的多组同时做不同的事:
    例 — Section 3 (能量0.55):
      调压气爆|阀 arc 60台 r1711 4seq  ← 第1气爆组:外弧,顺时针追逐
      调压气爆|阀 arc 48台 r1551 4seq  ← 第2气爆组:内弧,逆时针追逐
      → 两组形成空间对话(外层→内层卡农)

  编排级 = 哪个组、在哪个位置、做什么动作、和谁对话、什么时候进出。

纯 core，无 Qt/引擎依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from .project_profile import ProjectProfile, GroupProfile


# ═══════════════════════════════════════════════════════════════════
# 编排原子: Part = 一个具体的"演奏单元"
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Part:
    """一个编排声部 = (哪个组 + 怎么动 + 什么时候)。

    对标: 标杆中每个 clip 就是一个 Part。
    """
    # 身份
    part_id: str = ""            # 唯一标识
    device_type: str = ""        # def 名
    usage: str = ""              # 数控/泵/阀/RGB/功能
    group_ids: List[str] = field(default_factory=list)  # 具体 fixture IDs
    group_count: int = 0

    # 空间
    topology: str = "scattered"  # scattered/line/arc/full_ring
    radius: float = 0.0          # 环半径
    arc_span_deg: float = 180.0  # 弧度跨度(度)

    # 动作
    speed: float = 0.0           # DMX单位/秒
    direction: int = 1           # +1顺时针, -1逆时针
    sequential: str = ""         # "" / "2seq" / "4seq" / "8seq"
    motor_transition: bool = False

    # 时间
    start_s: float = 0.0
    end_s: float = 0.0
    fade_in_s: float = 0.8
    fade_out_s: float = 0.8

    # 色彩
    color_mode: str = "white"    # white/pure/color

    # 关系
    parent_part_id: str = ""     # 该声部"回答"哪个声部(空=独立)
    delay_after_parent_s: float = 0.0  # 延迟多久回应


# ═══════════════════════════════════════════════════════════════════
# 编排决策引擎
# ═══════════════════════════════════════════════════════════════════

class ArrangerV2:
    """编排级引擎 — 决策"哪组设备在哪个空间位置做什么动作"。

    对标标杆三层编排逻辑:
      L1: 声部选择 — 根据能量+打击+旋律,决定哪些组该活跃
      L2: 声部定位 — 每组分配到哪个空间位置(半径/弧度/拓扑)
      L3: 声部对话 — 组间关系(卡农/模仿/问答/齐奏)
    """

    def __init__(self, profile: ProjectProfile, seed: int = 0):
        self.profile = profile
        self.seed = seed

        # ── 按半径+设备类型索引所有组(为编排级选择准备) ──
        self._rings: Dict[str, List[GroupProfile]] = {}  # device_type → rings sorted by radius
        self._index_groups()

    def _index_groups(self) -> None:
        """将项目编组按设备类型+半径分组。"""
        by_device: Dict[str, List[GroupProfile]] = {}
        for g in self.profile.groups:
            dt = g.device_type or "unknown"
            by_device.setdefault(dt, []).append(g)

        for dt, groups in by_device.items():
            # 按半径排序: 小半径=内环, 大半径=外环
            groups.sort(key=lambda g: g.radius)
            self._rings[dt] = groups

    # ── L1: 声部选择 — 哪些组在当前段该活跃 ─────────────────

    def select_parts(
        self,
        energy: float,
        percussive: float,
        melodic: float = 0.1,
        *,
        section_duration_s: float = 8.0,
        section_idx: int = 0,
        _n_sections: int = 1,
    ) -> List[Part]:
        """为当前音乐段选择活跃的编排声部。

        对标规律:
          - 低能段: 仅外层大环, 稀疏散布 → 2-3 parts
          - 中能段: 中层环加入, 线形聚焦 → 4-5 parts
          - 高能段: 内外全开, 整环+弧 → 6-8 parts
          - 峰值段: 全环+多弧+电机过渡 → 8-12 parts (20 clips in benchmark!)
        """
        parts: List[Part] = []
        pid = 0

        # ── 每组可选的 GroupProfile 数 ──
        # 对标: 低能段仅1-2组, 高能段按设备类型选2-4组
        n_groups_per_device = max(1, int(energy * 4))  # 1~4组每种设备

        # ── 遍历每种设备类型 ──
        for device_type, rings in self._rings.items():
            caps = self._device_caps(device_type)

            # 设备在当前能量的活跃度判断
            if not self._should_device_be_active(device_type, caps, energy, percussive, melodic):
                continue

            # 选取该设备类型中哪些环(半径)该活跃
            selected_rings = self._select_rings_for_energy(rings, energy, n_groups_per_device)

            for ring in selected_rings:
                # ── 确定该组在当前段用什么用法(数控/泵/阀/RGB) ──
                usages = self._active_usages_for_device(caps, energy, percussive)

                for usage in usages:
                    pid += 1
                    part = Part(
                        part_id=f"p{pid:03d}",
                        device_type=device_type,
                        usage=usage,
                        group_ids=list(ring.member_ids),
                        group_count=ring.count,
                        radius=ring.radius,
                    )
                    # ── L2: 声部定位 — 拓扑/速度/空间参数 ──
                    self._apply_spatial_params(part, energy, ring)
                    # ── L3: 色彩 ──
                    part.color_mode = self._choose_color(energy, usage, percussive)
                    parts.append(part)

        # ── 峰值额外: 电机过渡声部 ──
        if energy >= 0.70 and melodic > 0.08:
            parts.extend(self._make_motor_transition_parts(energy, pid))

        # ── 气爆 seq 声部 ──
        if energy > 0.35 and percussive > 0.65:
            parts.extend(self._make_sequential_parts(energy, percussive, pid))

        # ── L3: 声部对话 — 组间关系 ──
        self._apply_counterpoint(parts, energy, section_idx)

        # ── L4: 力度弧线 — 用 PhraseDynamics 覆盖淡入淡出 ──
        self._apply_phrase_dynamics(parts, energy, section_duration_s, section_idx, _n_sections)

        return parts

    # ── 辅助方法 ──────────────────────────────────────────────

    def _device_caps(self, device_type: str) -> List[str]:
        """获取设备类型的能力列表。"""
        dt_info = self.profile.device_types.get(device_type, {})
        return dt_info.get("caps", [])

    def _should_device_be_active(
        self, device_type: str, caps: List[str], energy: float,
        percussive: float, melodic: float,
    ) -> bool:
        """判断设备类型在当前段是否该活跃。

        对标阈值:
          LED/RGB: energy>0.15（不是始终活跃! 极低能段LED也休息）
          数控(电机): energy>0.15
          泵: energy>0.30 (推迟 — 对标直到section2才出现泵)
          阀: energy>0.35
          气爆: energy>0.38 (对标: section3能量0.38才引入)
        """
        if "rgb" in caps:
            # LED/喷泉RGB: 极低能段也休息(对标: intro仅LED灯组, 喷泉RGB未开)
            if "LED" in device_type or "灯组" in device_type:
                return energy > 0.12  # LED灯组几乎始终开
            return energy > 0.20  # 喷泉RGB稍晚加入
        if "motor" in caps:
            return energy > 0.15 or melodic > 0.08
        if "pump" in caps:
            return energy > 0.28  # 对标: section2(0.30)才出现泵
        if "valve" in caps:
            # 纯阀(非气爆): 对标 section2-3 才加入
            if "pump" in caps:
                return energy > 0.35  # 气爆: section3(0.38)引入
            return energy > 0.30
        return energy > 0.20

    def _select_rings_for_energy(
        self, rings: List[GroupProfile], energy: float, max_rings: int,
    ) -> List[GroupProfile]:
        """从设备的所有环中选取当前段该活跃的。

        对标规律:
          低能(<0.25): 仅最大环(r最大)1个 — 弥散背景, 内环休息
          中低(0.25-0.40): 大环+中环, 2个
          中高(0.40-0.55): 大环+中环, 最多3个
          高能(>0.55): 全环(大+中+小)

        分类按该设备类型的环分布自适应（非全局绝对半径）。
        """
        if not rings:
            return []

        n = len(rings)
        # 按半径排序后分三等分: 外1/3, 中1/3, 内1/3
        sorted_rings = sorted(rings, key=lambda g: g.radius)
        third = max(1, n // 3)

        outer = sorted_rings[-third:] if n >= 2 else sorted_rings
        middle = sorted_rings[third:-third] if n >= 3 else []
        inner = sorted_rings[:third] if n >= 3 else []

        selected = []

        # 外层: 能量>0.10即加入（始终在,只要能见度够）
        if outer and energy > 0.10:
            selected.append(outer[-1])

        # 中层: 能量>0.25加入
        if energy > 0.25 and middle:
            n_mid = min(len(middle), max_rings - len(selected))
            if n_mid > 0:
                selected.extend(middle[-n_mid:])

        # 内层: 能量>0.55加入(chorus)
        if energy > 0.55 and inner:
            n_in = min(len(inner), max_rings - len(selected))
            if n_in > 0:
                selected.extend(inner[-n_in:])

        return selected[:max_rings]

    def _active_usages_for_device(
        self, caps: List[str], energy: float, percussive: float,
    ) -> List[str]:
        """确定设备在当前段该用哪些用法(数控/泵/阀/RGB/功能)。

        对标: 功能通道始终开, 其他按能量+设备能力判定。
        同一设备可同时有多种用法活跃(如 HJC二维同时出 数控+泵+RGB)。
        """
        usages = []

        # 功能通道: 始终存在(speed=0 静态)
        # (由 effect 引擎自动处理, 此处不显式生成)

        if "motor" in caps and energy > 0.15:
            usages.append("数控")
        if "pump" in caps and energy > 0.25:
            usages.append("泵")
        if "valve" in caps and energy > 0.30:
            usages.append("阀")
        if "rgb" in caps:
            usages.append("RGB")

        return usages

    def _apply_spatial_params(self, part: Part, energy: float, ring: GroupProfile) -> None:
        """L2: 为声部设定空间参数 — 拓扑+速度+弧度。

        对标拓扑律:
          低能(<0.25): scattered, 大半径, 慢速
          中能(0.25-0.40): line, 中半径
          中高能(0.40-0.55): arc, 中小半径, 240°跨度
          高能(>0.55): full_ring 或密集arc, 快速

        半径与速度关系(对标): 外环比内环慢——外围视觉面积大,太快会晕
        """
        r = ring.radius

        if energy < 0.25:
            part.topology = "scattered"
            part.speed = 10 + energy * 30
        elif energy < 0.40:
            part.topology = "line"
            part.speed = 30 + (energy - 0.25) * 100
        elif energy < 0.55:
            part.topology = "arc"
            part.arc_span_deg = 180 + energy * 80
            part.speed = 60 + (energy - 0.40) * 200
        else:
            part.topology = "full_ring" if r < 800 else "arc"
            part.arc_span_deg = 240 if r > 800 else 360
            part.speed = 80 + (energy - 0.55) * 400

        # 外环降速补偿: 半径越大视觉移动越快,需要降速
        if r > 1000:
            part.speed *= 0.5
        elif r > 600:
            part.speed *= 0.7

    @staticmethod
    def _choose_color(energy: float, usage: str, percussive: float) -> str:
        """对标色彩律。"""
        if "气爆" in usage or "阀" in usage:
            return "white"  # 水雾即白
        if usage == "泵":
            return "white" if percussive > 0.7 else "pure"
        if energy < 0.30:
            return "white"
        if energy < 0.55:
            return "pure"
        return "color"

    def _make_motor_transition_parts(self, energy: float, base_pid: int) -> List[Part]:
        """峰值段: 生成电机过渡声部(对标: 2par 电机过渡)。

        电机过渡 = 电机缓慢从一个角度摆到另一个角度,带长淡入(1.6-2.3s)。
        对标: 仅在 energy≥0.70 出现。
        """
        parts = []
        for dt, rings in self._rings.items():
            caps = self._device_caps(dt)
            if "motor" not in caps:
                continue
            for ring in rings[:2]:  # 最多2组做电机过渡
                pid = base_pid + len(parts) + 1
                p = Part(
                    part_id=f"p{pid:03d}",
                    device_type=dt, usage="数控",
                    group_ids=list(ring.member_ids),
                    group_count=ring.count,
                    topology="scattered" if ring.radius > 800 else "arc",
                    radius=ring.radius,
                    speed=30 + energy * 20,
                    motor_transition=True,
                    fade_in_s=1.6, fade_out_s=2.0,
                    color_mode="white",
                )
                parts.append(p)
        return parts

    def _make_sequential_parts(self, energy: float, percussive: float, base_pid: int) -> List[Part]:
        """气爆 seq 追逐声部: 多组不同半径/弧度的气爆交替追逐。

        对标: 同一段内可能有 2-4 个气爆 seq 组,不同弧,不同方向,形成空间对话。
        """
        parts = []
        seq_type = "8seq" if (energy > 0.65 and percussive > 0.75) else \
                   "4seq" if (energy > 0.50 and percussive > 0.65) else "2seq"

        # 找所有带 valve 的设备组
        seq_groups = []
        for dt, rings in self._rings.items():
            caps = self._device_caps(dt)
            if "valve" not in caps:
                continue
            for ring in rings:
                if ring.radius > 300:  # 太小半径不适合seq追逐
                    seq_groups.append((dt, ring))

        # 选2-4组做seq, 方向交替
        n_seq = min(len(seq_groups), 2 + int(energy * 3))
        for i, (dt, ring) in enumerate(seq_groups[:n_seq]):
            pid = base_pid + len(parts) + 1
            direction = 1 if i % 2 == 0 else -1  # 奇偶方向交替
            p = Part(
                part_id=f"p{pid:03d}",
                device_type=dt, usage="阀",
                group_ids=list(ring.member_ids),
                group_count=ring.count,
                topology="arc" if ring.radius > 800 else "full_ring",
                radius=ring.radius,
                arc_span_deg=180 + i * 40,
                speed=0,  # 气爆seq速度=0(静态爆炸, seq控制节奏)
                direction=direction,
                sequential=seq_type,
                fade_in_s=0.5, fade_out_s=0.6,
                color_mode="white",
            )
            parts.append(p)

        return parts

    def _apply_counterpoint(
        self, parts: List[Part], energy: float, section_idx: int,
    ) -> None:
        """L3: 声部对话 — 组间卡农/模仿/问答关系。

        对标模式:
          外层→内层卡农: 外环先动, delay后内环模仿(类似弦乐合奏的 entrance order)
          方向对位: 相邻环方向相反,增强视觉对比
          密度对话: 稀疏→密集→稀疏,随乐句呼吸
        """
        if len(parts) < 2:
            return

        # 按半径分组(内→外)
        by_radius = sorted(parts, key=lambda p: p.radius)

        # ── 卡农入场: 内环先动(小半径=快速), 外环延迟 ──
        # 对标: 数控经常先从小环开始, 然后扩展到大环
        delay_per_ring = section_idx * 0.1 + 0.15  # 每环延迟~150ms

        prev_radius = None
        ring_idx = 0
        for p in by_radius:
            if p.radius != prev_radius:
                ring_idx += 1
                prev_radius = p.radius
            # 越外层延迟越多
            p.delay_after_parent_s = ring_idx * delay_per_ring

        # ── 方向对位: 相邻环方向相反 ──
        for i, p in enumerate(by_radius):
            if i > 0 and p.radius != by_radius[i - 1].radius:
                p.direction = -by_radius[i - 1].direction

        # ── 问答对: seq 声部成对反向追逐 ──
        for i, p in enumerate(by_radius):
            if p.sequential and p.topology == "arc":
                for j in range(i + 1, len(by_radius)):
                    q = by_radius[j]
                    if q.device_type == p.device_type and q.sequential and q.parent_part_id == "":
                        q.parent_part_id = p.part_id
                        q.direction = -p.direction
                        break

    def _apply_phrase_dynamics(
        self, parts: List[Part], energy: float, duration_s: float,
        section_idx: int, n_sections: int,
    ) -> None:
        """L4: 用 PhraseDynamics 引擎覆盖淡入淡出值。

        对标标杆: 首段渐入(长淡入), 末段渐弱(长淡出+终止式渐慢)。
        """
        try:
            from .phrase_dynamics import PhraseDynamicsEngine
            dyn = PhraseDynamicsEngine.from_energy(
                section_energy=energy,
                duration_s=duration_s,
                is_first_section=(section_idx == 0),
                is_last_section=(section_idx == n_sections - 1),
            )
            for part in parts:
                level_at_enter = PhraseDynamicsEngine.level_at_time(dyn, 0.05)
                level_at_exit = PhraseDynamicsEngine.level_at_time(dyn, 0.95)
                part.fade_in_s = max(0.3, part.fade_in_s * (2.0 - level_at_enter))
                part.fade_out_s = max(0.3, part.fade_out_s * (2.0 - level_at_exit))
                if dyn.has_cadence:
                    part.speed *= dyn.ritardando_factor
        except ImportError:
            pass
