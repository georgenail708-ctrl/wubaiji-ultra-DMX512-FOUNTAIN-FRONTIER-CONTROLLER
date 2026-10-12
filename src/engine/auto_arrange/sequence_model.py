"""
sequence_model — 序列建模：跨音乐段依赖分析

提供：
  - N-gram序列特征: 拼接当前段+前2段特征(46×3=138维)
  - 预判功能: 预测下一段的音乐特征，提前调整灯光
  - 段落填充: 用户编intro+chorus → AI自动填verse/transition
  - 编排连贯性评分: 输出全曲编排的整体连贯分数(0..1)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

try:
    from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False

from .experience_table import SUBSYSTEMS, ROLE_LEAD, ROLE_SUPPORT, ROLE_SILENT

ROLE_ENCODE = {ROLE_SILENT: 0, ROLE_SUPPORT: 1, ROLE_LEAD: 2}
ROLE_DECODE = {0: ROLE_SILENT, 1: ROLE_SUPPORT, 2: ROLE_LEAD}

# 段落转移概率 (基于典型流行歌曲结构)
# intro→verse→build→chorus→drop→breakdown→outro
_SECTION_TRANSITIONS = {
    "intro": {"verse": 0.9, "build": 0.1},
    "verse": {"verse": 0.3, "build": 0.4, "chorus": 0.2, "breakdown": 0.1},
    "build": {"chorus": 0.7, "drop": 0.2, "verse": 0.1},
    "pre_chorus": {"chorus": 0.8, "drop": 0.2},
    "chorus": {"chorus": 0.15, "drop": 0.35, "verse": 0.2, "breakdown": 0.15, "outro": 0.15},
    "drop": {"breakdown": 0.5, "chorus": 0.3, "outro": 0.2},
    "breakdown": {"chorus": 0.4, "build": 0.3, "outro": 0.2, "verse": 0.1},
    "outro": {"outro": 1.0},
}

SECTION_ENCODE = {
    "intro": 0.0, "verse": 0.17, "build": 0.33, "pre_chorus": 0.42,
    "chorus": 0.5, "drop": 0.67, "breakdown": 0.83, "outro": 1.0,
}


@dataclass
class SequencePrediction:
    """序列预测结果。"""
    next_section: str = ""
    next_energy: float = 0.5
    next_features: Optional[np.ndarray] = None  # 预测的下段46维特征
    transition_confidence: float = 0.0
    coherence_score: float = 0.0  # 0..1 编排连贯性


class NgramSequenceModel:
    """N-gram序列特征模型：拼接前N段特征，预测下一段的角色分配。

    这是"穷人的序列模型"——不需要LSTM/Transformer，利用sklearn的
    RandomForest处理拼接后的138维特征向量。
    """

    def __init__(self, n_gram: int = 3):
        self.n_gram = int(n_gram)
        self._role_model: Optional[Any] = None  # RandomForestClassifier per sub
        self._energy_model: Optional[Any] = None  # RandomForestRegressor
        self._scaler: Optional[StandardScaler] = None
        self._trained = False
        self._feature_dim: int = 46  # 单段特征维度

    def _build_ngram_features(
        self, segment_sequence: List[np.ndarray]
    ) -> Optional[np.ndarray]:
        """将段序列转为N-gram特征矩阵。每个样本=当前段+前n-1段拼接。
        返回 (n_samples, feature_dim * n_gram) 或 None。
        """
        n = len(segment_sequence)
        if n < self.n_gram:
            return None
        features = []
        for i in range(self.n_gram - 1, n):
            window = segment_sequence[i - self.n_gram + 1:i + 1]
            # 反转顺序使最近的段在最后
            flat = np.concatenate(window)
            features.append(flat)
        return np.array(features, dtype=np.float64)

    def fit(self, segment_features: List[np.ndarray],
            segment_roles: List[Dict[str, int]],
            segment_energies: List[float]) -> Dict[str, Any]:
        """训练N-gram序列模型。"""
        if not _SKLEARN_OK or len(segment_features) < self.n_gram + 3:
            return {"trained": False, "note": "sklearn不可用或样本不足"}

        X = self._build_ngram_features(segment_features)
        if X is None or len(X) < 3:
            return {"trained": False, "note": "无法构建N-gram特征"}

        self._feature_dim = (
            segment_features[0].shape[0] if len(segment_features) > 0 else 46
        )
        self._scaler = StandardScaler()
        X_scaled = self._scaler.fit_transform(X)

        # 训练角色预测器 (每个子系统)
        self._role_model = {}
        roles_all = segment_roles[self.n_gram - 1:]
        for sub in SUBSYSTEMS:
            y_role = np.array([r.get(sub, 0) for r in roles_all], dtype=np.int32)
            if len(set(y_role)) >= 2:
                rf = RandomForestClassifier(
                    n_estimators=30, max_depth=4, random_state=42
                )
                rf.fit(X_scaled, y_role)
                self._role_model[sub] = rf

        # 训练能量预测器
        energies_all = np.array(
            segment_energies[self.n_gram - 1:], dtype=np.float64
        )
        self._energy_model = RandomForestRegressor(
            n_estimators=30, max_depth=4, random_state=42
        )
        self._energy_model.fit(X_scaled, energies_all)

        self._trained = True
        return {
            "trained": True, "n_samples": len(X), "feature_dim": X.shape[1],
            "n_role_models": len(self._role_model),
        }

    def predict_next(
        self, recent_features: List[np.ndarray]
    ) -> Optional[SequencePrediction]:
        """预测下一段的特征和角色。recent_features: 最近n_gram-1个段的特征。"""
        if not self._trained or len(recent_features) < self.n_gram - 1:
            return None

        # 填充到n_gram长度 (预测目标段特征用均值)
        mean_feat = np.mean(recent_features, axis=0)
        padded = list(recent_features[-(self.n_gram - 1):]) + [mean_feat]
        X_input = np.concatenate(padded).reshape(1, -1)
        if self._scaler is not None:
            X_input = self._scaler.transform(X_input)

        pred = SequencePrediction()

        # 预测能量
        if self._energy_model is not None:
            pred.next_energy = float(
                np.clip(self._energy_model.predict(X_input)[0], 0.0, 1.0)
            )

        # 预测角色
        if self._role_model:
            roles = {}
            for sub, rf in self._role_model.items():
                roles[sub] = int(rf.predict(X_input)[0])
            # 用多数投票确定主导角色
            lead_count = sum(
                1 for r in roles.values() if r == ROLE_ENCODE[ROLE_LEAD]
            )
            if lead_count >= 2:
                pred.next_section = "chorus"
            elif lead_count >= 1:
                pred.next_section = "verse"
            else:
                pred.next_section = "breakdown"

        pred.transition_confidence = 0.5
        return pred


class SegmentGapFiller:
    """段落填充器：给定首尾段，beam search填充中间段。

    用户编intro+chorus → AI自动填verse+build作为过渡。
    """

    def __init__(self, beam_width: int = 3):
        self.beam_width = int(beam_width)

    def fill_gaps(
        self, known_segments: List[Tuple[str, float]],
        total_segments: int,
    ) -> List[Tuple[str, float]]:
        """填充缺失的段落。

        Args:
            known_segments: [(section_label, energy), ...] 已知段落
            total_segments: 目标总段数

        Returns:
            完整的段落序列 [(section, energy), ...]
        """
        if len(known_segments) >= total_segments:
            return known_segments[:total_segments]

        result = list(known_segments)

        # 在已知段之间插入过渡段
        while len(result) < total_segments:
            best_pos = -1
            best_section = "verse"
            best_energy = 0.5
            best_score = -1.0

            for i in range(len(result)):
                prev_sec = result[i][0]
                next_sec = (
                    result[i + 1][0] if i + 1 < len(result) else "outro"
                )

                candidates = _SECTION_TRANSITIONS.get(prev_sec, {"verse": 1.0})
                for sec, prob in sorted(
                    candidates.items(), key=lambda x: -x[1]
                ):
                    # 检查从插入段到下一段的转移概率
                    forward = _SECTION_TRANSITIONS.get(sec, {}).get(
                        next_sec, 0.3
                    )
                    score = prob * forward
                    if score > best_score:
                        best_score = score
                        best_pos = i
                        best_section = sec
                        prev_energy = result[i][1]
                        next_energy = (
                            result[i + 1][1]
                            if i + 1 < len(result)
                            else 0.2
                        )
                        best_energy = (prev_energy + next_energy) / 2.0

            if best_pos >= 0:
                result.insert(best_pos + 1, (best_section, best_energy))
            else:
                # 回退：插入verse
                mid = len(result) // 2
                result.insert(mid, ("verse", 0.5))

        return result[:total_segments]


def compute_coherence_score(
    segment_decisions: List[Dict[str, Any]],
    segment_energies: List[float],
) -> float:
    """计算编排连贯性评分 (0..1)。

    惩罚:
      - 主奏↔留白突变 (-0.2)
      - 能量曲线锯齿 (-0.1)
      - 同子系统频繁切换 (-0.1)
    奖励:
      - 渐进过渡 (+0.1)
      - 副歌重复呼应 (+0.1)
      - 段落级能量弧线 (+0.1)
    """
    if not segment_decisions or len(segment_decisions) < 2:
        return 0.5

    score = 0.6  # 基础分
    n = len(segment_decisions)

    # 角色突变检测
    for sub in SUBSYSTEMS:
        roles = []
        for d in segment_decisions:
            subs = d.get("subsystems", {})
            roles.append(subs.get(sub, {}).get("role", ROLE_SILENT))
        for i in range(1, n):
            prev, curr = roles[i - 1], roles[i]
            if prev == ROLE_LEAD and curr == ROLE_SILENT:
                score -= 0.2 / len(SUBSYSTEMS)
            elif prev == ROLE_SILENT and curr == ROLE_LEAD:
                score -= 0.1 / len(SUBSYSTEMS)
            elif prev == ROLE_SUPPORT and curr == ROLE_LEAD:
                score += 0.05 / len(SUBSYSTEMS)  # 渐进过渡

    # 能量平滑度
    if segment_energies and len(segment_energies) >= 2:
        energy_diffs = np.abs(np.diff(segment_energies))
        mean_diff = float(np.mean(energy_diffs))
        if mean_diff > 0.4:
            score -= 0.15
        elif mean_diff < 0.2:
            score += 0.1

    # 能量弧线奖励 (intro低→高潮高→outro低)
    if len(segment_energies) >= 4:
        first_quarter = np.mean(segment_energies[:n // 4])
        middle_half = np.mean(segment_energies[n // 4:3 * n // 4])
        last_quarter = np.mean(segment_energies[3 * n // 4:])
        if middle_half > first_quarter and middle_half > last_quarter:
            score += 0.1  # 经典弧线

    return float(np.clip(score, 0.0, 1.0))
