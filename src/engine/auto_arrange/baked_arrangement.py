"""
baked_arrangement — 预烘焙编曲方案（纯数据层 + 进程内单例 store）

设计哲学（用户定义）：
  一首歌导入后，自动编曲就**离线一次性把整首歌算完**，生成一份固定的编曲方案；
  之后每次播放都回放同一份 → 效果完全确定、可复现。只有点"重新编排"才会
  （带新随机种子）重算一份。

方案 = 一条按时间排序的段落时间线，每段绑定一个 ArrangementDecision 快照：
    BakedSegment   { t_start, t_end, section, energy, decision }
    BakedArrangement { audio_path, audio_sig, seed, created_at, bpm, duration, segments[] }

回放时（ManagedPlayback baked 模式）：按播放位置 pos 查 segment_at(pos)，
段落切换时应用该段 decision。纯查表，无实时重型分析 → 不卡主线程。

持久化：仿 programmer_cue_store 范式，本模块只提供 to_dict/from_dict，
随项目存档由上层（main_window）接入 Project.baked_arrangement_data（opaque dict）。

本模块纯数据 + 逻辑，无 Qt / 引擎依赖，可独立单测。
"""
from __future__ import annotations

import time
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from .arrangement_director import ArrangementDecision


def audio_signature(path: str) -> str:
    """音频文件指纹：路径(规范化) + 大小 + mtime。
    换了音频文件 / 文件被替换 → 指纹变 → 旧方案失效需重烘焙。
    取不到文件信息时退回纯路径，保证仍可比较。"""
    try:
        p = os.path.abspath(os.fspath(path or ""))
    except Exception:
        return str(path or "")
    if not p:
        return ""
    try:
        st = os.stat(p)
        return f"{p}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        return p


@dataclass
class BakedSegment:
    """一段编曲：覆盖 [t_start, t_end) 秒，应用 decision。"""
    t_start: float = 0.0
    t_end: float = 0.0
    section: str = ""
    energy: float = 0.5
    decision: Optional[ArrangementDecision] = None

    def to_dict(self) -> dict:
        return {
            "t_start": float(self.t_start),
            "t_end": float(self.t_end),
            "section": str(self.section),
            "energy": float(self.energy),
            "decision": self.decision.to_dict() if self.decision is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BakedSegment":
        d = d or {}
        dec_d = d.get("decision")
        return cls(
            t_start=float(d.get("t_start", 0.0) or 0.0),
            t_end=float(d.get("t_end", 0.0) or 0.0),
            section=str(d.get("section", "")),
            energy=float(d.get("energy", 0.5) or 0.5),
            decision=ArrangementDecision.from_dict(dec_d) if isinstance(dec_d, dict) else None,
        )


@dataclass
class BakedArrangement:
    """整首歌的预烘焙编曲方案。"""
    audio_path: str = ""
    audio_sig: str = ""              # 音频指纹，换歌失效判定用
    seed: int = 0                    # 本次烘焙的随机种子（重新编排换种子→不同变体）
    created_at: float = field(default_factory=lambda: time.time())
    bpm: float = 120.0
    duration: float = 0.0
    profile_summary: str = ""        # 项目画像摘要（给依据日志）
    segments: List[BakedSegment] = field(default_factory=list)

    # ── 回放查表 ───────────────────────────────────────────────────────────
    def segment_at(self, pos: float) -> Optional[BakedSegment]:
        """返回播放位置 pos(秒) 命中的段落。线性扫描（段落数通常 < 100）。"""
        if not self.segments:
            return None
        p = float(pos)
        for seg in self.segments:
            if seg.t_start <= p < seg.t_end:
                return seg
        # 超出末尾：归到最后一段（尾声延续）；早于开头：归到第一段
        if p >= self.segments[-1].t_end:
            return self.segments[-1]
        if p < self.segments[0].t_start:
            return self.segments[0]
        return None

    def is_valid_for(self, audio_path: str) -> bool:
        """该方案是否仍适用于给定音频（指纹一致）。"""
        if not self.segments:
            return False
        return self.audio_sig == audio_signature(audio_path)

    def summary_text(self) -> str:
        secs = len(self.segments)
        return f"{secs} 段 · {self.duration:.0f}s · BPM~{self.bpm:.0f} · seed={self.seed}"

    def to_dict(self) -> dict:
        return {
            "audio_path": str(self.audio_path),
            "audio_sig": str(self.audio_sig),
            "seed": int(self.seed),
            "created_at": float(self.created_at),
            "bpm": float(self.bpm),
            "duration": float(self.duration),
            "profile_summary": str(self.profile_summary),
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BakedArrangement":
        d = d or {}
        try:
            created = float(d.get("created_at", time.time()))
        except (TypeError, ValueError):
            created = time.time()
        segs = [BakedSegment.from_dict(s) for s in (d.get("segments", []) or [])
                if isinstance(s, dict)]
        return cls(
            audio_path=str(d.get("audio_path", "")),
            audio_sig=str(d.get("audio_sig", "")),
            seed=int(d.get("seed", 0) or 0),
            created_at=created,
            bpm=float(d.get("bpm", 120.0) or 120.0),
            duration=float(d.get("duration", 0.0) or 0.0),
            profile_summary=str(d.get("profile_summary", "")),
            segments=segs,
        )


class BakedArrangementStore:
    """进程内当前项目的预烘焙方案容器（单条，绑定当前音频）。"""

    def __init__(self):
        self._current: Optional[BakedArrangement] = None

    def get(self) -> Optional[BakedArrangement]:
        return self._current

    def set(self, plan: Optional[BakedArrangement]) -> None:
        self._current = plan

    def clear(self) -> None:
        self._current = None

    def has_valid_for(self, audio_path: str) -> bool:
        return self._current is not None and self._current.is_valid_for(audio_path)

    # ── 持久化 ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        if self._current is None:
            return {}
        return {"current": self._current.to_dict()}

    def from_dict(self, d: dict) -> None:
        self._current = None
        if not isinstance(d, dict):
            return
        cur = d.get("current")
        if isinstance(cur, dict) and cur.get("segments"):
            self._current = BakedArrangement.from_dict(cur)


# ══════════════════════════════════════════════════════════════════════════
#  声部制方案(L3 产物)—— 每声部一条决策时间线,供 L4 渲染成分轨 clip
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class VoiceClipWindow:
    """某声部的一个动作窗口(将渲染成一个 clip)。decision 是 VoiceDecision.to_dict()。"""
    t_start: float = 0.0
    t_end: float = 0.0
    character: str = ""
    decision: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"t_start": float(self.t_start), "t_end": float(self.t_end),
                "character": str(self.character), "decision": dict(self.decision or {})}

    @classmethod
    def from_dict(cls, d: dict) -> "VoiceClipWindow":
        d = d or {}
        return cls(
            t_start=float(d.get("t_start", 0.0) or 0.0),
            t_end=float(d.get("t_end", 0.0) or 0.0),
            character=str(d.get("character", "")),
            decision=dict(d.get("decision", {}) or {}))


@dataclass
class BakedVoiceArrangement:
    """声部制整曲方案:每声部一条稀疏动作窗口时间线。

    voices: {voice_key: [VoiceClipWindow,...]}  —— 只在有动作处有窗口(稀疏,像鼓MIDI)。
    voice_meta: {voice_key: {device_type,usage,voice_class}}  —— 供建轨。
    """
    audio_path: str = ""
    audio_sig: str = ""
    seed: int = 0
    created_at: float = field(default_factory=lambda: time.time())
    bpm: float = 120.0
    duration: float = 0.0
    profile_summary: str = ""
    voices: Dict[str, List[VoiceClipWindow]] = field(default_factory=dict)
    voice_meta: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def is_valid_for(self, audio_path: str) -> bool:
        if not self.voices:
            return False
        return self.audio_sig == audio_signature(audio_path)

    def window_count(self) -> int:
        return sum(len(w) for w in self.voices.values())

    def summary_text(self) -> str:
        return (f"{len(self.voices)} 声部 · {self.window_count()} 动作 · "
                f"{self.duration:.0f}s · BPM~{self.bpm:.0f} · seed={self.seed}")

    def to_dict(self) -> dict:
        return {
            "audio_path": str(self.audio_path),
            "audio_sig": str(self.audio_sig),
            "seed": int(self.seed),
            "created_at": float(self.created_at),
            "bpm": float(self.bpm),
            "duration": float(self.duration),
            "profile_summary": str(self.profile_summary),
            "voices": {k: [w.to_dict() for w in ws] for k, ws in self.voices.items()},
            "voice_meta": {k: dict(v) for k, v in self.voice_meta.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BakedVoiceArrangement":
        d = d or {}
        try:
            created = float(d.get("created_at", time.time()))
        except (TypeError, ValueError):
            created = time.time()
        voices = {}
        for k, ws in (d.get("voices", {}) or {}).items():
            voices[k] = [VoiceClipWindow.from_dict(w) for w in (ws or []) if isinstance(w, dict)]
        return cls(
            audio_path=str(d.get("audio_path", "")),
            audio_sig=str(d.get("audio_sig", "")),
            seed=int(d.get("seed", 0) or 0),
            created_at=created,
            bpm=float(d.get("bpm", 120.0) or 120.0),
            duration=float(d.get("duration", 0.0) or 0.0),
            profile_summary=str(d.get("profile_summary", "")),
            voices=voices,
            voice_meta={k: dict(v) for k, v in (d.get("voice_meta", {}) or {}).items()})


# ── 进程内单例 ────────────────────────────────────────────────────────────
_STORE_SINGLETON: Optional[BakedArrangementStore] = None

def get_baked_arrangement_store() -> BakedArrangementStore:
    """返回进程内唯一的预烘焙方案库实例。"""
    global _STORE_SINGLETON
    if _STORE_SINGLETON is None:
        _STORE_SINGLETON = BakedArrangementStore()
    return _STORE_SINGLETON
