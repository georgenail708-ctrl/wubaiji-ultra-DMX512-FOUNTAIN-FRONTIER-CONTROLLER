"""
case_retrieval — 检索式模仿(小样本学用户风格)

把标杆项目的真实编排做成案例库(benchmark_caselib.json:每音乐段→真实声部类配置)。
给新歌一段的音乐特征,检索最相似的标杆案例,作为编舞决策的**强先验**:
"你在相似音乐里开了这些声部类、用了这些速度/拓扑,我就倾向照做",再由弹性库微调。

按 voice_class(motor/pump/valve/fountain_rgb/matrix_rgb)索引,不绑设备名 → 换项目也能用
(模板是地基不是天花板)。纯 core,无依赖。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

# 检索特征维度 + 权重(能量/打击最重,旋律次之)
_FEAT_KEYS = ["e", "perc", "voiced", "centroid", "kick", "hat"]
_FEAT_W = {"e": 2.0, "perc": 1.8, "voiced": 1.2, "centroid": 0.8, "kick": 1.0, "hat": 0.6}


def _caselib_path() -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "benchmark_caselib.json")
    return cand if os.path.isfile(cand) else None


class CaseLibrary:
    """标杆案例库 + 最近案例检索。"""

    def __init__(self, cases: Optional[List[dict]] = None):
        self.cases: List[dict] = cases or []

    @classmethod
    def load(cls, path: Optional[str] = None) -> "CaseLibrary":
        path = path or _caselib_path()
        if not path or not os.path.isfile(path):
            return cls([])
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return cls(list(d.get("cases", []) or []))
        except Exception:
            return cls([])

    @property
    def available(self) -> bool:
        return bool(self.cases)

    def nearest(self, feat: Dict[str, float]) -> Optional[dict]:
        """按加权欧氏距离找最近案例。feat 需含 _FEAT_KEYS(缺的按0.5)。"""
        c, _w = self.nearest_weighted(feat)
        return c

    def nearest_weighted(self, feat: Dict[str, float]):
        """找最近案例 + 相似度权重 weight∈[0,1]。

        weight 按距离门控:音乐与案例越像→weight 越接近1(强参考);
        越不像(不同风格)→weight 衰减趋0,先验几乎不起作用,改由音乐自身驱动。
        返回 (case 或 None, weight)。
        """
        if not self.cases:
            return None, 0.0
        best = None
        best_d = float("inf")
        for c in self.cases:
            cf = c.get("feat", {})
            d = 0.0
            for k in _FEAT_KEYS:
                w = _FEAT_W.get(k, 1.0)
                dv = float(feat.get(k, 0.5)) - float(cf.get(k, 0.5))
                d += w * dv * dv
            if d < best_d:
                best_d = d
                best = c
        # 距离→相似度:dist 是加权平方和;开方后用软阈衰减。
        # 总权重 sum(_FEAT_W)≈7.4,各维差0.3级别→dist~0.7。以 dist0.35 为半衰。
        import math
        dist = math.sqrt(max(0.0, best_d))
        weight = 1.0 / (1.0 + (dist / 0.35) ** 2)   # dist=0→1, dist=0.35→0.5, dist大→0
        return best, max(0.0, min(1.0, weight))


def char_to_feat(char: Any) -> Dict[str, float]:
    """从 CharacterSegment 提取检索特征向量(与案例库特征同口径)。"""
    e = float(getattr(char, "energy_rank", 0.5) or 0.5)
    perc = float(getattr(char, "perc_density", 0.5) or 0.5)
    # perc_density 是原始尺度,归一靠经验夹一下
    perc = max(0.0, min(1.0, perc))
    voiced = 1.0 if bool(getattr(char, "has_melody", False)) else \
        float(getattr(char, "voiced", 0.0) or 0.0)
    centroid = float(getattr(char, "centroid", 1500.0) or 1500.0) / 4000.0
    return {
        "e": e, "perc": perc, "voiced": max(0.0, min(1.0, voiced)),
        "centroid": max(0.0, min(1.0, centroid)),
        "kick": min(1.0, perc), "hat": min(1.0, perc * 0.6),
    }


# 进程内单例(避免每段重读文件)
_LIB_SINGLETON: Optional[CaseLibrary] = None


def get_case_library() -> CaseLibrary:
    global _LIB_SINGLETON
    if _LIB_SINGLETON is None:
        _LIB_SINGLETON = CaseLibrary.load()
    return _LIB_SINGLETON
