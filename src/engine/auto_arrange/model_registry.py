"""
model_registry — 模型版本管理：时间戳checkpoint、回滚、指标追踪
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import joblib
import numpy as np


@dataclass
class CheckpointEntry:
    timestamp: float = 0.0
    label: str = ""
    path: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    note: str = ""


class ModelRegistry:
    """模型checkpoint注册表：时间戳版本管理 + 自动修剪。"""

    CHECKPOINT_DIR = "ml_models/checkpoints"
    INDEX_PATH = "ml_models/checkpoint_index.json"
    MAX_CHECKPOINTS = 20

    def __init__(self):
        self._entries: List[CheckpointEntry] = []
        os.makedirs(self.CHECKPOINT_DIR, exist_ok=True)
        self._load_index()

    def save(self, model_manager: Any, label: str = "", metrics: Optional[Dict] = None,
             note: str = "") -> str:
        """保存checkpoint，返回文件路径。"""
        ts = time.time()
        ts_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))
        filename = f"arrangement_model_{ts_str}.joblib"
        path = os.path.join(self.CHECKPOINT_DIR, filename)
        model_manager.save(path)

        entry = CheckpointEntry(
            timestamp=ts, label=label, path=path,
            metrics=metrics or {}, note=note,
        )
        self._entries.append(entry)
        self._prune()
        self._save_index()
        return path

    def load_latest(self, model_manager: Any) -> bool:
        """加载最新checkpoint。"""
        if not self._entries:
            return False
        latest = self._entries[-1]
        return model_manager.load(latest.path)

    def load_version(self, model_manager: Any, timestamp: float) -> bool:
        """加载指定时间戳的checkpoint。"""
        for entry in self._entries:
            if abs(entry.timestamp - timestamp) < 1.0:
                return model_manager.load(entry.path)
        return False

    def load_best(self, model_manager: Any, metric: str, operator: str = "max") -> bool:
        """加载指定指标最优的checkpoint。"""
        if not self._entries:
            return False
        best_entry = None
        best_val = -float('inf') if operator == "max" else float('inf')
        for entry in self._entries:
            val = entry.metrics.get(metric)
            if val is None:
                continue
            if operator == "max" and val > best_val:
                best_val = val
                best_entry = entry
            elif operator == "min" and val < best_val:
                best_val = val
                best_entry = entry
        if best_entry is not None:
            return model_manager.load(best_entry.path)
        return False

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        return [
            {"timestamp": e.timestamp,
             "label": e.label,
             "metrics": e.metrics,
             "note": e.note,
             "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.timestamp))}
            for e in self._entries
        ]

    def _prune(self):
        """保留最近MAX_CHECKPOINTS个。"""
        if len(self._entries) > self.MAX_CHECKPOINTS:
            to_remove = self._entries[:-self.MAX_CHECKPOINTS]
            for entry in to_remove:
                if os.path.isfile(entry.path):
                    try:
                        os.remove(entry.path)
                    except OSError:
                        pass
            self._entries = self._entries[-self.MAX_CHECKPOINTS:]

    def _load_index(self):
        if not os.path.isfile(self.INDEX_PATH):
            return
        try:
            with open(self.INDEX_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._entries = []
            for d in data:
                if os.path.isfile(d.get("path", "")):
                    self._entries.append(CheckpointEntry(
                        timestamp=float(d.get("timestamp", 0)),
                        label=str(d.get("label", "")),
                        path=str(d.get("path", "")),
                        metrics=d.get("metrics", {}),
                        note=str(d.get("note", "")),
                    ))
        except Exception:
            self._entries = []

    def _save_index(self):
        try:
            data = [
                {"timestamp": e.timestamp, "label": e.label,
                 "path": e.path, "metrics": e.metrics, "note": e.note}
                for e in self._entries
            ]
            with open(self.INDEX_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass
