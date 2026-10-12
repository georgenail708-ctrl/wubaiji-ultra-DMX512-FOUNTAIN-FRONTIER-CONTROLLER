# -*- coding: utf-8 -*-
"""EffectCache 的展示型 property（mixin，从 effect_cache 拆出压 500 行门禁）。

is_latch/is_flash/effective_key/is_bound/fade_seconds/priority_level/
effective_mode/node_count/panels/fixture_ids/title_label/subtitle/created_label
——全部纯读展示，无状态写入；EffectCache 继承本 mixin，调用方零改动。
"""
from __future__ import annotations

import time
from typing import List

from .trigger_keys import normalize_key


#: 优先级档上限（0 = 未设，不参与压制；EffectCache.priority 语义见 effect_cache）
PRIORITY_MAX = 5

#: 淡入淡出秒数上限（过长会让人误以为"没反应"）
FADE_MAX = 30.0


class EffectCacheLabels:
    """展示型 property 集（宿主字段：mode/key_binding/fade_in/priority/nodes…）。"""

    def is_latch(self) -> bool:
        """自锁/开关式（点一下保持、再点关闭）？"""
        return self.effective_mode() == "latch"

    def is_flash(self) -> bool:
        """点动/瞬时式（鼠标按住才应用、松手即释放）？"""
        return self.effective_mode() == "flash"

    def effective_key(self) -> str:
        """归一化后的绑定键（非法/缺失 = ""）——**所有消费方都读这个**。

        ★ 与 `effective_mode()` 同款纪律：dataclass 直接赋值绕过校验，故不依赖存进去的
          原始串，统一在读取时归一化（单一真源 = `trigger_keys.normalize_key`）。
        """
        return normalize_key(self.key_binding)

    def is_bound(self) -> bool:
        """是否已绑定键盘按键（读归一化值）。"""
        return bool(self.effective_key())

    def fade_seconds(self) -> float:
        """归一化淡入淡出秒数（夹到 [0, FADE_MAX]）。"""
        try:
            v = float(self.fade_in or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(float(FADE_MAX), v))

    def priority_level(self) -> int:
        """归一化优先级（夹到 [0, PRIORITY_MAX]；非法/缺失 = 0 不参与压制）。

        ★ 与 `effective_mode()` 同款纪律：dataclass 直接赋值绕过校验，
          统一在读取时归一化，消费方（scene_takeover 压制）只读这里。
        """
        try:
            v = int(self.priority or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(int(PRIORITY_MAX), v))

    def effective_mode(self) -> str:
        """归一化后的模式（非法/缺失/"hold" 一律回落 latch）。

        ★ "hold" 是老工程的旧值：语义与 latch 相同（点一下保持），故回落 latch。

        ★ 单一真源：所有消费方（调度台点击、菜单勾选、卡片角标）都读这里，
          避免各处各写一遍 `!= "hold"` 之类的判断而漂移。
        """
        m = str(self.mode or "latch").lower()
        return m if m in self.MODES else "latch"

    # ── 只读视图 ──────────────────────────────────────────────────────────
    def node_count(self) -> int:
        return len(self.nodes)

    def panels(self) -> List[str]:
        """涉及的面板（去重保序），用于卡片副标题"3 节点 · 2 面板"。"""
        seen: List[str] = []
        for entry in self.nodes:
            p = str((entry.get("node", {}) or {}).get("panel", "") or "")
            if p and p not in seen:
                seen.append(p)
        return seen

    def fixture_ids(self) -> List[str]:
        """全部涉及设备 id（去重保序）。还原前可据此核对设备是否仍在项目里。"""
        seen: List[str] = []
        out: List[str] = []
        for entry in self.nodes:
            for fid in (entry.get("node", {}) or {}).get("fixture_ids", []) or []:
                sf = str(fid)
                if sf and sf not in seen:
                    seen.append(sf); out.append(sf)
        return out

    def title_label(self) -> str:
        return f"{self.name}"

    def subtitle(self) -> str:
        return f"{self.node_count()} 节点 · {len(self.panels())} 面板"

    def created_label(self) -> str:
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(self.created_at))
        except Exception:
            return ""

