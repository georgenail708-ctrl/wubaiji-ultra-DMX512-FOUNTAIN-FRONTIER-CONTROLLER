"""
param_envelope.py — 通用参数包络（D1）
=========================================
功能 1「通用参数包络」的数据层。与 effect_engine 解耦：

  - ParamEnvelope        一条「时间 → 归一化值[0,1]」的周期曲线（手画 / 推杆录制）
  - ParamEnvelopeStore   增删改 + 绑定表(preset_id, param_key)->envelope_id + 序列化
  - get_param_envelope_store()  全局单例

设计要点
--------
* 曲线值统一归一化到 [0,1]，与具体参数的取值范围无关，因此一条包络可复用到
  任意数值参数上。引擎在取参时把 [0,1] 线性映射回该参数的 (min_val, max_val)。
* 复用 models.CurveMapping 存控制点（x=相位[0,1]，y=归一化值[0,1]），获得现成的
  线性插值 / evaluate / to_dict / from_dict。
* value_at(t) 用 (t % duration)/duration 把连续时间折叠到一个周期，effect 播放时
  自然「一直循环这个周期」。
* 绑定表对 630 个 compute 完全透明：引擎只是在调用 compute 前，把绑定参数的常数
  替换成包络当前值；compute 仍然只看到一个 float。
"""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

try:
    # 复用项目已有的曲线模型（控制点 + 线性插值 + 序列化）。
    from ..core.models import CurveMapping, CurvePoint
except Exception:  # pragma: no cover - 兜底，保证本模块可独立导入
    CurveMapping = None  # type: ignore
    CurvePoint = None    # type: ignore


def _clamp01(v: float) -> float:
    try:
        v = float(v)
    except Exception:
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


@dataclass
class ParamEnvelope:
    """一条通用参数包络。

    duration : 一个完整周期的时长（秒）。
    curve    : CurveMapping，控制点 x=相位[0,1]、y=归一化值[0,1]。
    source   : "manual"（先定时长再手画）或 "recorded"（推杆录制）。
    points   : 仅当 models.CurveMapping 不可用时的兜底采样点 [(x,y), ...]。
    """
    id: str = field(default_factory=lambda: "env_" + uuid.uuid4().hex[:12])
    name: str = "包络"
    duration: float = 4.0
    source: str = "manual"
    curve: Any = None
    points: List[List[float]] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: time.time())

    def __post_init__(self):
        if self.duration is None or float(self.duration) <= 0.0:
            self.duration = 4.0
        if self.curve is None and CurveMapping is not None:
            # 默认一条「下→上」的线性斜坡，便于直观看到效果。
            self.curve = CurveMapping(
                points=[CurvePoint(0.0, 0.0), CurvePoint(1.0, 1.0)],
                source=self.source or "manual",
            )

    # ── 求值 ─────────────────────────────────────────────────────────────────
    def value_at(self, t: float) -> float:
        """返回时刻 t 的归一化值 [0,1]，按 duration 周期循环。"""
        dur = float(self.duration) if self.duration else 4.0
        if dur <= 0.0:
            dur = 4.0
        try:
            phase = (float(t) % dur) / dur
        except Exception:
            phase = 0.0
        return _clamp01(self._eval_phase(phase))

    def _eval_phase(self, phase: float) -> float:
        phase = _clamp01(phase)
        if self.curve is not None and hasattr(self.curve, "evaluate"):
            try:
                return float(self.curve.evaluate(phase))
            except Exception:
                pass
        # 兜底：用 points 做线性插值。
        pts = sorted(self.points or [], key=lambda p: p[0])
        if not pts:
            return 0.0
        if phase <= pts[0][0]:
            return pts[0][1]
        if phase >= pts[-1][0]:
            return pts[-1][1]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if x0 <= phase <= x1:
                k = (phase - x0) / (x1 - x0) if x1 != x0 else 0.0
                return y0 + k * (y1 - y0)
        return pts[-1][1]

    # ── 序列化 ───────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "duration": float(self.duration),
            "source": self.source,
            "created_at": float(self.created_at),
        }
        if self.curve is not None and hasattr(self.curve, "to_dict"):
            d["curve"] = self.curve.to_dict()
        else:
            d["points"] = [[float(x), float(y)] for x, y in (self.points or [])]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ParamEnvelope":
        d = dict(d or {})
        curve = None
        pts: List[List[float]] = []
        if "curve" in d and d["curve"] and CurveMapping is not None:
            try:
                curve = CurveMapping.from_dict(d["curve"])
            except Exception:
                curve = None
        if curve is None:
            for p in (d.get("points", []) or []):
                try:
                    pts.append([float(p[0]), float(p[1])])
                except Exception:
                    continue
        env = cls(
            id=str(d.get("id") or ("env_" + uuid.uuid4().hex[:12])),
            name=str(d.get("name", "包络")),
            duration=float(d.get("duration", 4.0) or 4.0),
            source=str(d.get("source", "manual")),
            curve=curve,
            points=pts,
            created_at=float(d.get("created_at", time.time()) or time.time()),
        )
        return env


class ParamEnvelopeStore:
    """包络仓库 + 绑定表。

    绑定结构：_bindings[preset_id][param_key] = {"env": envelope_id,
                                                  "lo": float|None, "hi": float|None}
    同一 (preset_id, param_key) 同时只允许一条包络（覆盖语义）。
    lo/hi 为可选「输出子范围」：包络归一值[0,1] 线性映射到 [lo,hi]（而非参数全程
    min..max）。任一为 None 时该端回退参数自身 min/max。这样一条 0..1 曲线可只在
    参数的有用子区间内变化（例如速度只在 3~8Hz 摆动，而非 0~20Hz 全程）。
    旧工程序列化里该值可能是裸字符串 env_id（无子范围），读取时自动归一成 dict。
    """

    def __init__(self):
        self._envelopes: Dict[str, ParamEnvelope] = {}
        # preset_id -> {param_key -> {"env":id, "lo":float|None, "hi":float|None}}
        self._bindings: Dict[str, Dict[str, dict]] = {}

    @staticmethod
    def _norm_entry(v) -> dict:
        """把绑定值归一成 {"env":id, "lo":?, "hi":?}（兼容旧裸字符串）。"""
        if isinstance(v, dict):
            env = str(v.get("env", "") or "")
            lo = v.get("lo", None)
            hi = v.get("hi", None)
            try:
                lo = float(lo) if lo is not None else None
            except Exception:
                lo = None
            try:
                hi = float(hi) if hi is not None else None
            except Exception:
                hi = None
            return {"env": env, "lo": lo, "hi": hi}
        return {"env": str(v or ""), "lo": None, "hi": None}

    # ── 包络增删改查 ──────────────────────────────────────────────────────────
    def add(self, env: ParamEnvelope) -> ParamEnvelope:
        if env is None:
            return env
        if not env.id:
            env.id = "env_" + uuid.uuid4().hex[:12]
        self._envelopes[env.id] = env
        return env

    def create(self, name: str = "包络", duration: float = 4.0,
               source: str = "manual") -> ParamEnvelope:
        env = ParamEnvelope(name=name, duration=duration, source=source)
        return self.add(env)

    def remove(self, env_id: str) -> None:
        self._envelopes.pop(env_id, None)
        # 同步移除所有指向它的绑定。
        for preset_id in list(self._bindings.keys()):
            pmap = self._bindings.get(preset_id, {})
            for key in [k for k, v in pmap.items() if self._norm_entry(v)["env"] == env_id]:
                pmap.pop(key, None)
            if not pmap:
                self._bindings.pop(preset_id, None)

    def rename(self, env_id: str, new_name: str) -> None:
        env = self._envelopes.get(env_id)
        if env is not None:
            env.name = str(new_name or env.name)

    def get(self, env_id: str) -> Optional[ParamEnvelope]:
        return self._envelopes.get(env_id)

    def all(self) -> List[ParamEnvelope]:
        return sorted(self._envelopes.values(), key=lambda e: e.created_at)

    # ── 绑定表 ────────────────────────────────────────────────────────────────
    def bind(self, preset_id: str, param_key: str, env_id: str,
             out_lo: Optional[float] = None, out_hi: Optional[float] = None) -> None:
        preset_id = str(preset_id or "")
        param_key = str(param_key or "")
        if not preset_id or not param_key or env_id not in self._envelopes:
            return
        entry = {"env": str(env_id), "lo": None, "hi": None}
        if out_lo is not None:
            try:
                entry["lo"] = float(out_lo)
            except Exception:
                entry["lo"] = None
        if out_hi is not None:
            try:
                entry["hi"] = float(out_hi)
            except Exception:
                entry["hi"] = None
        self._bindings.setdefault(preset_id, {})[param_key] = entry

    def unbind(self, preset_id: str, param_key: str) -> None:
        pmap = self._bindings.get(str(preset_id or ""))
        if pmap is not None:
            pmap.pop(str(param_key or ""), None)
            if not pmap:
                self._bindings.pop(str(preset_id or ""), None)

    def get_bindings(self, preset_id: str) -> Dict[str, str]:
        """返回 {param_key: envelope_id}（只含仍存在的包络）。供引擎取参用。"""
        pmap = self._bindings.get(str(preset_id or ""), {})
        if not pmap:
            return {}
        out: Dict[str, str] = {}
        for k, v in pmap.items():
            env_id = self._norm_entry(v)["env"]
            if env_id in self._envelopes:
                out[k] = env_id
        return out

    def get_binding_range(self, preset_id: str, param_key: str):
        """返回该绑定的 (out_lo, out_hi)（任一可能为 None=回退参数 min/max）；
        无绑定或无子范围时返回 None。"""
        pmap = self._bindings.get(str(preset_id or ""), {})
        v = pmap.get(str(param_key or ""))
        if v is None:
            return None
        e = self._norm_entry(v)
        if e["lo"] is None and e["hi"] is None:
            return None
        return (e["lo"], e["hi"])

    def binding_for(self, preset_id: str, param_key: str) -> Optional[str]:
        return self.get_bindings(preset_id).get(str(param_key or ""))

    # ── 序列化 ───────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        # 写出绑定时剔除失效引用，保证往返干净。
        clean_bindings: Dict[str, Dict[str, dict]] = {}
        for preset_id, pmap in self._bindings.items():
            kept = {}
            for k, v in (pmap or {}).items():
                e = self._norm_entry(v)
                if e["env"] in self._envelopes:
                    kept[k] = e
            if kept:
                clean_bindings[preset_id] = kept
        return {
            "version": 2,
            "envelopes": [e.to_dict() for e in self.all()],
            "bindings": clean_bindings,
        }

    def from_dict(self, d: Optional[dict]) -> None:
        d = dict(d or {})
        self._envelopes.clear()
        self._bindings.clear()
        for ed in (d.get("envelopes", []) or []):
            try:
                env = ParamEnvelope.from_dict(ed)
                self._envelopes[env.id] = env
            except Exception:
                continue
        for preset_id, pmap in (d.get("bindings", {}) or {}).items():
            for param_key, raw in (pmap or {}).items():
                e = self._norm_entry(raw)
                if e["env"] in self._envelopes:
                    self._bindings.setdefault(str(preset_id), {})[str(param_key)] = e

    def clear(self) -> None:
        self._envelopes.clear()
        self._bindings.clear()


# ── 全局单例 ───────────────────────────────────────────────────────────────────
_store_instance: Optional[ParamEnvelopeStore] = None


def get_param_envelope_store() -> ParamEnvelopeStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = ParamEnvelopeStore()
    return _store_instance
