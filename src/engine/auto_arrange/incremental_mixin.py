"""
incremental_mixin — 增量学习支持：避免每次从头训练
"""
from __future__ import annotations

from typing import Dict, List, Optional, Any
from abc import ABC, abstractmethod

import numpy as np


class IncrementalModel(ABC):
    """增量学习抽象基类。"""

    @abstractmethod
    def partial_fit(self, X: np.ndarray, y: Any) -> None:
        """增量更新模型参数。"""
        ...

    @abstractmethod
    def is_trained(self) -> bool:
        ...


class IncrementalWrapper:
    """通用增量学习包装器。对于支持partial_fit的sklearn模型直接调用；
    对于不支持的模型，积累数据后在阈值触发时热重训练。
    """

    def __init__(self, retrain_threshold: int = 50):
        self._threshold = int(retrain_threshold)
        self._buffer_X: List[np.ndarray] = []
        self._buffer_y: List[Any] = []
        self._retrain_callback: Optional[Any] = None

    def set_retrain_callback(self, callback):
        """设置重训练回调: callback(X_accumulated, y_accumulated) -> None。"""
        self._retrain_callback = callback

    def add_sample(self, X_row: np.ndarray, y_val: Any) -> bool:
        """添加一个样本。返回True表示触发了重训练。"""
        self._buffer_X.append(X_row)
        self._buffer_y.append(y_val)
        if len(self._buffer_X) >= self._threshold:
            self._trigger_retrain()
            return True
        return False

    def _trigger_retrain(self):
        if self._retrain_callback is None or not self._buffer_X:
            return
        X = np.array(self._buffer_X, dtype=np.float64)
        try:
            self._retrain_callback(X, self._buffer_y)
        except Exception:
            pass
        else:
            self.clear_buffer()

    def force_retrain(self):
        self._trigger_retrain()

    def pending_count(self) -> int:
        return len(self._buffer_X)

    def clear_buffer(self):
        self._buffer_X.clear()
        self._buffer_y.clear()
