"""
arrangement_rl — 强化学习在线反馈系统 (Ch16)

K-摇臂赌博机 (K-Armed Bandit) + 用户反馈收集器。
每次用户手动调整自动编排 → 记录为一条反馈 → 积累足够后重训练。

核心思想:
  - 每个"编排动作"(子系统×角色×preset组合) = 一个摇臂
  - 用户接受 → reward=+1; 用户改成别的 → reward=-1
  - ε-贪心策略: 95% 选历史最好的, 5% 随机尝试(防止陷入局部最优)

纯 core, 无 Qt/引擎依赖。
"""
from __future__ import annotations

import time
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
# Feedback data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FeedbackEntry:
    """One piece of user feedback on an auto-arrangement decision.

    ★ role_accepted 和 preset_accepted 已解耦：用户可能接受角色但换预设，反之亦然。
    """
    timestamp: float = 0.0
    section: str = ""              # music section (verse/chorus/...)
    energy: float = 0.5
    subsystem: str = ""            # light/pump/motor/valve
    auto_role: str = ""            # AI's suggested role
    auto_preset: str = ""          # AI's suggested preset
    auto_level: float = 0.0
    user_role: str = ""            # user's actual choice
    user_preset: str = ""          # user's actual preset
    user_level: float = 0.0
    accepted: bool = True          # deprecated: 保留向后兼容，等同 role_accepted and preset_accepted
    role_accepted: bool = True     # auto_role == user_role
    preset_accepted: bool = True   # auto_preset == user_preset
    project_name: str = ""
    source: str = ""               # "ml_blend" | "manual" | "auto"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "section": self.section,
            "energy": round(self.energy, 3),
            "subsystem": self.subsystem,
            "auto_role": self.auto_role,
            "auto_preset": self.auto_preset,
            "auto_level": round(self.auto_level, 3),
            "user_role": self.user_role,
            "user_preset": self.user_preset,
            "user_level": round(self.user_level, 3),
            "accepted": self.accepted,
            "role_accepted": self.role_accepted,
            "preset_accepted": self.preset_accepted,
            "project_name": self.project_name,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FeedbackEntry":
        d = d or {}
        role_ok = bool(d.get("role_accepted", d.get("accepted", True)))
        preset_ok = bool(d.get("preset_accepted", d.get("accepted", True)))
        return cls(
            timestamp=float(d.get("timestamp", 0) or 0),
            section=str(d.get("section", "")),
            energy=float(d.get("energy", 0.5) or 0.5),
            subsystem=str(d.get("subsystem", "")),
            auto_role=str(d.get("auto_role", "")),
            auto_preset=str(d.get("auto_preset", "")),
            auto_level=float(d.get("auto_level", 0) or 0),
            user_role=str(d.get("user_role", "")),
            user_preset=str(d.get("user_preset", "")),
            user_level=float(d.get("user_level", 0) or 0),
            accepted=role_ok and preset_ok,
            role_accepted=role_ok,
            preset_accepted=preset_ok,
            project_name=str(d.get("project_name", "")),
            source=str(d.get("source", "")),
        )


# ═══════════════════════════════════════════════════════════════════════
# Feedback Collector
# ═══════════════════════════════════════════════════════════════════════

class FeedbackCollector:
    """Collects user feedback on auto-arrangement decisions.

    Usage:
        collector = FeedbackCollector()
        collector.record(section="chorus", sub="motor",
                         auto_role="lead", user_role="lead", accepted=True)
        collector.save("feedback_log.json")
    """

    def __init__(self, log_path: str = "ml_models/feedback_log.json"):
        self.log_path = log_path
        self.entries: List[FeedbackEntry] = []
        self._load()

    def record(
        self,
        section: str,
        subsystem: str,
        auto_role: str = "",
        auto_preset: str = "",
        auto_level: float = 0.0,
        user_role: str = "",
        user_preset: str = "",
        user_level: float = 0.0,
        energy: float = 0.5,
        project_name: str = "",
        source: str = "",
    ) -> FeedbackEntry:
        """Record one feedback entry. Returns the entry.

        ★ role 和 preset 接受度独立判断，提供更精确的奖励信号。
        """
        role_ok = (auto_role == user_role)
        preset_ok = (auto_preset == user_preset)
        entry = FeedbackEntry(
            timestamp=time.time(),
            section=section,
            energy=energy,
            subsystem=subsystem,
            auto_role=auto_role,
            auto_preset=auto_preset,
            auto_level=auto_level,
            user_role=user_role,
            user_preset=user_preset,
            user_level=user_level,
            accepted=role_ok and preset_ok,
            role_accepted=role_ok,
            preset_accepted=preset_ok,
            project_name=project_name,
            source=source,
        )
        self.entries.append(entry)
        return entry

    def save(self, path: str = ""):
        """Persist feedback log to JSON file."""
        target = path or self.log_path
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        data = [e.to_dict() for e in self.entries]
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def _load(self):
        """Load existing feedback from disk."""
        if not os.path.isfile(self.log_path):
            return
        try:
            with open(self.log_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.entries = [FeedbackEntry.from_dict(d) for d in data]
        except Exception:
            self.entries = []

    def count(self) -> int:
        return len(self.entries)

    def acceptance_rate(self) -> float:
        """Overall user acceptance rate of AI suggestions."""
        if not self.entries:
            return 0.0
        return sum(1 for e in self.entries if e.accepted) / len(self.entries)

    def acceptance_by_subsystem(self) -> Dict[str, float]:
        """Acceptance rate per subsystem."""
        counts: Dict[str, List[bool]] = {}
        for e in self.entries:
            counts.setdefault(e.subsystem, []).append(e.accepted)
        return {sub: sum(vals) / len(vals) for sub, vals in counts.items()}

    def summary(self) -> Dict[str, Any]:
        """Human-readable summary of feedback statistics."""
        return {
            "total": self.count(),
            "acceptance_rate": round(self.acceptance_rate(), 3),
            "by_subsystem": {sub: round(rate, 3)
                             for sub, rate in self.acceptance_by_subsystem().items()},
            "last_updated": time.strftime("%Y-%m-%d %H:%M") if self.entries else "never",
        }


# ═══════════════════════════════════════════════════════════════════════
# K-Armed Bandit (RL Agent)
# ═══════════════════════════════════════════════════════════════════════

class KArmedBandit:
    """K-摇臂赌博机: online learning from user feedback.

    Each "arm" = a specific (section, subsystem, role, preset) tuple.
    The agent learns which arms get positive user feedback.

    Strategy: ε-greedy — exploit best arm 95% of the time, explore 5%.
    """

    def __init__(self, epsilon: float = 0.05):
        self.epsilon = float(np.clip(epsilon, 0.0, 1.0))
        self._arms: Dict[str, Dict[str, Any]] = {}  # arm_key → {count, total_reward, q_value}
        self._total_pulls: int = 0

    @staticmethod
    def _arm_key(section: str, subsystem: str, role: str = "", preset: str = "") -> str:
        """Encode an arm as a string key."""
        return f"{section}|{subsystem}|{role}|{preset}"

    def record_feedback(self, section: str, subsystem: str,
                        auto_role: str, auto_preset: str,
                        accepted: bool) -> None:
        """Update Q-values based on user feedback."""
        key = self._arm_key(section, subsystem, auto_role, auto_preset)
        if key not in self._arms:
            self._arms[key] = {"count": 0, "total_reward": 0.0, "q_value": 0.0}

        arm = self._arms[key]
        reward = 1.0 if accepted else -0.5
        arm["count"] += 1
        arm["total_reward"] += reward
        arm["q_value"] = arm["total_reward"] / arm["count"]
        self._total_pulls += 1

    def best_arm(self, section: str, subsystem: str,
                 candidates: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
        """Select the best arm among candidates using ε-greedy.

        Args:
            section: current music section.
            subsystem: target subsystem.
            candidates: list of (role, preset) tuples to choose from.

        Returns:
            (role, preset) or None if no candidates.
        """
        if not candidates:
            return None

        # ε-greedy: explore
        if np.random.random() < self.epsilon:
            idx = np.random.randint(0, len(candidates))
            return candidates[idx]

        # exploit: pick candidate with highest Q-value
        best = None
        best_q = -999.0
        for role, preset in candidates:
            key = self._arm_key(section, subsystem, role, preset)
            q = self._arms.get(key, {}).get("q_value", 0.0)
            if q > best_q:
                best_q = q
                best = (role, preset)

        return best if best is not None else candidates[0]

    def needs_retraining(self, threshold: int = 50) -> bool:
        """Check if enough new feedback has accumulated to warrant retraining."""
        return self._total_pulls >= threshold

    def stats(self) -> Dict[str, Any]:
        """Return agent statistics."""
        n_arms = len(self._arms)
        if n_arms == 0:
            return {"n_arms": 0, "total_pulls": 0, "avg_q": 0.0, "top_arms": []}
        q_values = [a["q_value"] for a in self._arms.values()]
        top_n = 5
        sorted_arms = sorted(self._arms.items(), key=lambda x: -x[1]["q_value"])[:top_n]
        return {
            "n_arms": n_arms,
            "total_pulls": self._total_pulls,
            "avg_q": round(float(np.mean(q_values)), 3),
            "max_q": round(float(np.max(q_values)), 3),
            "top_arms": [
                {"key": k, "q": round(a["q_value"], 3), "pulls": a["count"]}
                for k, a in sorted_arms
            ],
        }

    def save(self, path: str = "ml_models/bandit_state.json"):
        """Save bandit state to JSON."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {
            "epsilon": self.epsilon,
            "total_pulls": self._total_pulls,
            "arms": self._arms,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def load(self, path: str = "ml_models/bandit_state.json") -> bool:
        """Load bandit state from JSON. Returns success."""
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.epsilon = float(data.get("epsilon", 0.05))
            self._total_pulls = int(data.get("total_pulls", 0))
            self._arms = data.get("arms", {})
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════
# UCBBandit — Upper Confidence Bound (替代ε-贪心)
# ═══════════════════════════════════════════════════════════════════════

class UCBBandit:
    """Upper Confidence Bound Bandit — 替代ε-贪心，智能平衡探索与利用。

    选择公式: ucb = Q_i + c * sqrt(log(N) / n_i)
    c = sqrt(2) 是理论最优探索参数。
    乐观初始化: 新arm的Q值初始化为1.0，确保每个arm都被探索一次。
    """

    def __init__(self, c: float = 1.414):
        self.c = float(c)  # sqrt(2) ≈ 1.414
        self._arms: Dict[str, Dict[str, Any]] = {}
        self._total_pulls: int = 0

    @staticmethod
    def _arm_key(section: str, subsystem: str, role: str = "", preset: str = "") -> str:
        return f"{section}|{subsystem}|{role}|{preset}"

    def record_feedback(self, section: str, subsystem: str,
                        auto_role: str, auto_preset: str,
                        accepted: bool) -> None:
        """更新Q值: incremental average Q += (reward - Q) / count."""
        key = self._arm_key(section, subsystem, auto_role, auto_preset)
        if key not in self._arms:
            # 乐观初始化: Q=1.0 确保新arm被优先探索
            self._arms[key] = {"count": 0, "q_value": 1.0}
        arm = self._arms[key]
        reward = 1.0 if accepted else -0.5
        arm["count"] += 1
        arm["q_value"] += (reward - arm["q_value"]) / arm["count"]
        self._total_pulls += 1

    def best_arm(self, section: str, subsystem: str,
                 candidates: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
        """UCB选择: argmax(Q_i + c * sqrt(log(N) / n_i))."""
        if not candidates:
            return None
        self._total_pulls += 1  # 每次选择也算一次pull
        best = None
        best_ucb = -float('inf')
        for role, preset in candidates:
            key = self._arm_key(section, subsystem, role, preset)
            arm = self._arms.get(key, {"count": 0, "q_value": 1.0})
            n = max(arm["count"], 1)
            ucb = arm["q_value"] + self.c * np.sqrt(np.log(max(self._total_pulls, 2)) / n)
            if ucb > best_ucb:
                best_ucb = ucb
                best = (role, preset)
        return best if best is not None else candidates[0]

    def needs_retraining(self, threshold: int = 50) -> bool:
        return self._total_pulls >= threshold

    def stats(self) -> Dict[str, Any]:
        n_arms = len(self._arms)
        if n_arms == 0:
            return {"n_arms": 0, "total_pulls": self._total_pulls, "avg_q": 0.0, "top_arms": []}
        q_values = [a["q_value"] for a in self._arms.values()]
        top_n = 5
        sorted_arms = sorted(self._arms.items(), key=lambda x: -x[1]["q_value"])[:top_n]
        return {
            "n_arms": n_arms, "total_pulls": self._total_pulls,
            "avg_q": round(float(np.mean(q_values)), 3),
            "max_q": round(float(np.max(q_values)), 3),
            "top_arms": [{"key": k, "q": round(a["q_value"], 3), "pulls": a["count"]}
                        for k, a in sorted_arms],
        }

    def save(self, path: str = "ml_models/ucb_bandit_state.json"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {"c": self.c, "total_pulls": self._total_pulls, "arms": self._arms}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def load(self, path: str = "ml_models/ucb_bandit_state.json") -> bool:
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.c = float(data.get("c", 1.414))
            self._total_pulls = int(data.get("total_pulls", 0))
            self._arms = data.get("arms", {})
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════
# LinUCBBandit — Contextual Bandit (考虑音乐上下文)
# ═══════════════════════════════════════════════════════════════════════

class LinUCBBandit:
    """Contextual Bandit (LinUCB) — 考虑音乐上下文的bandit。

    上下文向量: [section_encoded, energy, percussive, melodic] (4维)
    每个arm独立岭回归，选UCB最大arm。
    """

    def __init__(self, alpha: float = 0.5, context_dim: int = 4):
        self.alpha = float(alpha)
        self.context_dim = int(context_dim)
        # 每个arm: {A: (d,d) 协方差矩阵逆, b: (d,) 奖励向量, theta: (d,) 回归系数}
        self._arms: Dict[str, Dict[str, Any]] = {}
        self._total_pulls: int = 0
        self._section_encode = {
            "intro": 0.0, "verse": 0.17, "build": 0.33, "pre_chorus": 0.42,
            "chorus": 0.5, "drop": 0.67, "breakdown": 0.83, "outro": 1.0,
        }

    @staticmethod
    def _arm_key(section: str, subsystem: str, role: str = "", preset: str = "") -> str:
        return f"{section}|{subsystem}|{role}|{preset}"

    def _build_context(self, section: str, energy: float = 0.5,
                       percussive: float = 0.5, melodic: float = 0.1) -> np.ndarray:
        sec_enc = self._section_encode.get(section, 0.5)
        return np.array([sec_enc, energy, percussive, melodic], dtype=np.float64)

    def _init_arm(self) -> Dict[str, Any]:
        d = self.context_dim
        return {
            "A": np.eye(d) * 0.1,     # 协方差矩阵 (d,d) — 小的初始值=高不确定性
            "b": np.zeros(d),           # 奖励向量 (d,)
            "theta": np.zeros(d),       # 回归系数
            "count": 0,
        }

    def record_feedback(self, section: str, subsystem: str,
                        auto_role: str, auto_preset: str,
                        accepted: bool, energy: float = 0.5,
                        percussive: float = 0.5, melodic: float = 0.1) -> None:
        """在线更新: A += x*x^T, b += r*x, theta = A^-1 * b."""
        key = self._arm_key(section, subsystem, auto_role, auto_preset)
        if key not in self._arms:
            self._arms[key] = self._init_arm()
        arm = self._arms[key]
        x = self._build_context(section, energy, percussive, melodic)
        reward = 1.0 if accepted else -0.5
        arm["A"] += np.outer(x, x)
        arm["b"] += reward * x
        arm["theta"] = np.linalg.solve(arm["A"], arm["b"])
        arm["count"] += 1
        self._total_pulls += 1

    def best_arm(self, section: str, subsystem: str,
                 candidates: List[Tuple[str, str]],
                 energy: float = 0.5, percussive: float = 0.5,
                 melodic: float = 0.1) -> Optional[Tuple[str, str]]:
        """LinUCB选择: argmax(theta^T * x + alpha * sqrt(x^T * A^-1 * x))."""
        if not candidates:
            return None
        x = self._build_context(section, energy, percussive, melodic)
        best = None
        best_score = -float('inf')
        for role, preset in candidates:
            key = self._arm_key(section, subsystem, role, preset)
            arm = self._arms.get(key)
            if arm is None:
                # 未探索的arm → 高UCB (乐观初始化)
                score = self.alpha * np.sqrt(x @ (np.eye(self.context_dim) * 10.0) @ x)
            else:
                predicted = arm["theta"] @ x
                # sqrt(x^T * A^-1 * x)
                try:
                    A_inv_x = np.linalg.solve(arm["A"], x)
                    uncertainty = self.alpha * np.sqrt(x @ A_inv_x + 1e-8)
                except np.linalg.LinAlgError:
                    uncertainty = self.alpha * 2.0
                score = predicted + uncertainty
            if score > best_score:
                best_score = score
                best = (role, preset)
        return best if best is not None else candidates[0]

    def stats(self) -> Dict[str, Any]:
        n_arms = len(self._arms)
        if n_arms == 0:
            return {"n_arms": 0, "total_pulls": self._total_pulls, "avg_theta_norm": 0.0}
        theta_norms = [np.linalg.norm(a["theta"]) for a in self._arms.values()]
        return {
            "n_arms": n_arms, "total_pulls": self._total_pulls,
            "avg_theta_norm": round(float(np.mean(theta_norms)), 3),
            "top_arms": sorted(
                [{"key": k, "count": a["count"],
                  "theta_norm": round(float(np.linalg.norm(a["theta"])), 3)}
                 for k, a in self._arms.items()],
                key=lambda x: -x["count"])[:5],
        }

    def save(self, path: str = "ml_models/linucb_state.json"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {
            "alpha": self.alpha, "total_pulls": self._total_pulls,
            "arms": {k: {"A": v["A"].tolist(), "b": v["b"].tolist(),
                         "theta": v["theta"].tolist(), "count": v["count"]}
                    for k, v in self._arms.items()},
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def load(self, path: str = "ml_models/linucb_state.json") -> bool:
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.alpha = float(data.get("alpha", 0.5))
            self._total_pulls = int(data.get("total_pulls", 0))
            self._arms = {}
            for k, v in data.get("arms", {}).items():
                self._arms[k] = {
                    "A": np.array(v["A"]), "b": np.array(v["b"]),
                    "theta": np.array(v["theta"]), "count": v["count"],
                }
            return True
        except Exception:
            return False