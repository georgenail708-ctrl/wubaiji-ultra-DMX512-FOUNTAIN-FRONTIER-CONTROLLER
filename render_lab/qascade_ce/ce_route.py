"""qascade_ce.ce_route — CE 覆盖内核 (ce_route_coverage) 高层封装。

把 render_lab/csrc/ce_route.c 的 C 内核包装为 Python 可用的持久 ctx:

  * 设备坐标: (n_dev, 2) float64 (注册序)
  * 路径集合: 每路径 = 顶点列表 [(x, y), ...]
  * 每帧 trains[] = (path_idx, nose_s, car_unit, car_count, radius)

覆盖语义与 ai-memory/_audit_r1/_route_runner_spec.md §CE 内核契约逐字一致:
  车厢 i 覆盖弧区间 = [nose_s-(i+1)*car_unit, nose_s-i*car_unit];
  设备激活当 (投影 s 在任一区间 且 横向距离 d<=radius);
  强度 = max over cars of max(255*(1-d/radius), 1); 无覆盖 = 0。

投影预计算在 sync() (C OpenMP 按设备并行), 每帧查询在 frame() (C 串行 + SIMD,
<0.5ms @11382×8)。DLL/符号缺失 → _ensure() False, 调用方回退 numpy。
"""
from __future__ import annotations

import ctypes
import logging

import numpy as np

log = logging.getLogger(__name__)

_D = ctypes.POINTER(ctypes.c_double)
_I = ctypes.POINTER(ctypes.c_int)
_U = ctypes.POINTER(ctypes.c_uint8)

MAX_PATHS = 32
MAX_DEVICES = 20000


class CERouteCoverage:
    """CE 覆盖内核持久上下文 (设备+路径投影预计算 + 每帧覆盖查询)。"""

    def __init__(self) -> None:
        self._engine = None
        self._ctx = None
        self._dev_x: np.ndarray | None = None
        self._dev_y: np.ndarray | None = None
        self._n_dev = 0
        self._n_paths = 0
        self._path_ids: list[str] = []
        self._order_s: np.ndarray | None = None
        self._train_arr = None

    def _ensure(self) -> bool:
        """懒加载 CE 引擎 + 创建 ctx; DLL/符号缺失 → False。"""
        if self._engine is not None:
            return True
        try:
            from .ctypes_api import CE_Route_Train, load_engine
        except Exception as exc:  # pragma: no cover
            log.debug("ce_route: ctypes_api import failed: %s", exc)
            return False
        e = load_engine()
        if e is None or e.ce_route_ctx_create is None or e.ce_route_frame is None:
            return False
        ctx = e.ce_route_ctx_create()
        if not ctx:
            return False
        self._engine = e
        self._ctx = ctx
        self._train_arr = (CE_Route_Train * MAX_PATHS)()
        return True

    @property
    def ready(self) -> bool:
        return self._ensure()

    @property
    def path_ids(self) -> list[str]:
        return list(self._path_ids)

    def sync(self, device_pos, path_ids, paths) -> bool:
        """同步设备坐标与路径集合并做投影预计算 (任一变化时调用)。

        device_pos: (n_dev, 2) float64 (注册序)
        path_ids: list[str] 路径名序 (与 frame() 的 path_idx 对应)
        paths: list[list[(x, y)]]
        """
        if not self._ensure():
            return False
        dev = np.ascontiguousarray(device_pos, dtype=np.float64).reshape(-1, 2)
        n_dev = dev.shape[0]
        if n_dev > MAX_DEVICES:
            return False
        path_ids = [str(p) for p in path_ids]
        paths = [list(p) for p in paths]
        n_paths = len(paths)
        if n_paths > MAX_PATHS:
            return False

        path_off = np.zeros(n_paths + 1, dtype=np.int32)
        total = 0
        for i, p in enumerate(paths):
            total += len(p)
            path_off[i + 1] = total
        path_x = np.empty(total, dtype=np.float64)
        path_y = np.empty(total, dtype=np.float64)
        k = 0
        for p in paths:
            for (x, y) in p:
                path_x[k] = float(x)
                path_y[k] = float(y)
                k += 1

        e = self._engine
        rc = e.ce_route_paths_set(
            self._ctx,
            path_x.ctypes.data_as(_D) if total > 0 else None,
            path_y.ctypes.data_as(_D) if total > 0 else None,
            path_off.ctypes.data_as(_I), n_paths)
        if rc != 0:
            return False
        self._dev_x = np.ascontiguousarray(dev[:, 0], dtype=np.float64)
        self._dev_y = np.ascontiguousarray(dev[:, 1], dtype=np.float64)
        rc = e.ce_route_devices_project(
            self._ctx,
            self._dev_x.ctypes.data_as(_D) if n_dev > 0 else None,
            self._dev_y.ctypes.data_as(_D) if n_dev > 0 else None,
            n_dev)
        if rc != 0:
            return False
        self._n_dev = n_dev
        self._n_paths = n_paths
        self._path_ids = path_ids
        # 路径 0 的投影弧长 → active_ordered_ids 车尾→车头排序用
        if n_dev > 0 and n_paths > 0 and e.ce_route_devices_get is not None:
            s_full = np.empty(n_dev * n_paths, dtype=np.float64)
            d_full = np.empty(n_dev * n_paths, dtype=np.float64)
            e.ce_route_devices_get(
                self._ctx, s_full.ctypes.data_as(_D), d_full.ctypes.data_as(_D))
            self._order_s = np.ascontiguousarray(
                s_full.reshape(n_dev, n_paths)[:, 0], dtype=np.float64)
        else:
            self._order_s = np.zeros(n_dev, dtype=np.float64)
        return True

    @property
    def order_s(self) -> np.ndarray:
        """路径 0 的投影弧长 (n_dev,) float64; 未同步 → 全 0。"""
        if self._order_s is None:
            return np.zeros(self._n_dev if self._n_dev > 0 else 0, dtype=np.float64)
        return self._order_s

    def frame(self, out, trains) -> bool:
        """每帧覆盖查询。trains = list[(path_idx, nose_s, car_unit, car_count, radius)]。

        out: np.uint8 [n_dev] 输出强度数组 (按设备索引)。
        """
        if self._engine is None or self._ctx is None or self._n_dev <= 0:
            return False
        n = min(len(trains), MAX_PATHS)
        arr = self._train_arr
        for i in range(n):
            t = trains[i]
            arr[i].path_id = int(t[0])
            arr[i].nose_s = float(t[1])
            arr[i].car_unit = float(t[2])
            arr[i].car_count = int(t[3])
            arr[i].radius = float(t[4])
        rc = self._engine.ce_route_frame(
            self._ctx, arr, n, out.ctypes.data_as(_U))
        return rc == 0

    def close(self) -> None:
        if self._ctx is not None and self._engine is not None:
            try:
                self._engine.ce_route_ctx_destroy(self._ctx)
            except Exception:  # pragma: no cover
                pass
        self._ctx = None
        self._engine = None
        self._train_arr = None
        self._dev_x = None
        self._dev_y = None
        self._order_s = None
        self._n_dev = 0
        self._n_paths = 0

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover
            pass


_DEFAULT_COV: CERouteCoverage | None = None


def ce_route_coverage(out, trains, device_pos=None, path_ids=None, paths=None):
    """模块级单例封装 (供 runner_engine._compute_coverage_ce 以 fn(out, trains)
    调用)。设备/路径几何在 device_pos/path_ids/paths 提供时先同步, 否则沿用上次
    同步几何。成功返回 True; 任何失败抛 RuntimeError (调用方 try/except 回退 numpy,
    避免把全零 coverage 误当真实结果)。

    trains: list[dict] = {path_id(str), nose_s, car_unit, car_count, radius}
    """
    global _DEFAULT_COV
    if _DEFAULT_COV is None:
        cov = CERouteCoverage()
        if not cov.ready:
            raise RuntimeError("CE 覆盖内核 DLL/符号不可用")
        _DEFAULT_COV = cov
    if device_pos is not None:
        if not _DEFAULT_COV.sync(device_pos, list(path_ids or []), list(paths or [])):
            raise RuntimeError("CE 覆盖内核同步失败")
        _DEFAULT_COV._id2idx = {
            pid: i for i, pid in enumerate(_DEFAULT_COV.path_ids)}
    idx_map = getattr(_DEFAULT_COV, "_id2idx", None)
    if not idx_map:
        raise RuntimeError("CE 覆盖内核未同步几何 (缺 device_pos/path_ids/paths)")
    tup = []
    for tr in trains:
        pid = tr.get("path_id")
        idx = idx_map.get(str(pid)) if pid is not None else None
        if idx is None:
            continue
        tup.append((idx, float(tr["nose_s"]), float(tr["car_unit"]),
                    int(tr["car_count"]), float(tr["radius"])))
    if not _DEFAULT_COV.frame(out, tup):
        raise RuntimeError("CE 覆盖内核 frame 失败")
    return True
