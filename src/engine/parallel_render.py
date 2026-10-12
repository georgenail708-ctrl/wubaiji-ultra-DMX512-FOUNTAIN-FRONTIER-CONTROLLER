# -*- coding: utf-8 -*-
"""时间线"渲染并锁定"的多进程并行加速。

串行路径每帧 ~8ms、7000+ 帧串行 → ~55s，全程单核。本模块把帧区间切成 N 段交给
N 个 worker 进程各渲一段（离线批处理，非实时，各进程独立 GIL → 真并行），再合并。

逐字节等价保证：worker 调用的是引擎里同一个 _render_timeline_frame_range（与串行完全
相同的逐帧代码），只是区间不同。拼接后与串行结果逐字节一致（由 A/B 测试背书）。

进程边界数据传递：
  - 主 → worker：工程序列化成临时 JSON（project.to_dict()，含内存未存改动），
    worker 端 Project.load() 重建。规避 QObject 不可 pickle + 磁盘旧版本两个坑。
  - worker → 主：各自的 (frame_cache, record_source_cache) 分片 pickle 到临时文件，
    只回传文件路径（避免 multiprocessing 管道对 ~180MB 负载的双拷贝瓶颈）。

Windows 为 spawn 模式：worker 入口必须是模块级函数、参数全可 pickle（均为 str/int/float）。
"""
from __future__ import annotations

import os
import sys
import math
import pickle
import tempfile
import multiprocessing as mp
from typing import Optional, Callable, Dict, Tuple, List, Any


def _default_worker_count(frame_count: int) -> int:
    """选 worker 数：留 1-2 核给主进程/系统；帧太少不值得多开。"""
    try:
        cpu = os.cpu_count() or 4
    except Exception:
        cpu = 4
    # 留一核；上限防止 spawn 冷启动开销吞掉收益
    n = max(1, min(cpu - 1, 12))
    # 每 worker 至少摊到 ~200 帧才划算
    n = max(1, min(n, max(1, frame_count // 200)))
    return n


def _chunk_ranges(frame_count: int, n_workers: int) -> List[Tuple[int, int]]:
    """把 [0, frame_count) 尽量均分成 n_workers 段连续区间。"""
    n_workers = max(1, n_workers)
    base = frame_count // n_workers
    rem = frame_count % n_workers
    ranges: List[Tuple[int, int]] = []
    start = 0
    for i in range(n_workers):
        size = base + (1 if i < rem else 0)
        if size <= 0:
            continue
        ranges.append((start, start + size))
        start += size
    return ranges


# 预热帧数：worker 从 start-LEAD_IN 开始渲染以暖机跨帧累积状态(fade-in seam 链等),
# 只保留 [start,stop) 的帧。实测该状态在 ~50 帧内收敛,取 256 为 5× 安全裕度。
# 由 A/B 等价测试逐字节背书;不足时测试会立即暴露。
LEAD_IN_FRAMES = 256


def _worker_render_range(args: Tuple[str, int, int, float, float, int, str, str]) -> Tuple[str, int, int]:
    """worker 进程入口：加载工程 → headless 引擎 → precompile → 暖机预热 → 渲染 [start,stop) → 落盘。

    返回 (out_path, start_idx, n_frames)；失败时抛异常由主进程捕获回退。
    """
    (project_json_path, start_idx, stop_idx, interval, end, frame_count,
     out_path, repo_root) = args

    # spawn 子进程需自行装 sys.path（找到 src 包）
    src_dir = os.path.join(repo_root, "src")
    for p in (src_dir, repo_root):
        if p not in sys.path:
            sys.path.insert(0, p)
    # 与串行/harness 一致：关异步 worker，纯同步渲染
    os.environ["QASCADE_OUTPUT_WORKER"] = "0"
    # 防止子进程再触发并行渲染造成递归 fork
    os.environ["QASCADE_PARALLEL_RENDER"] = "0"

    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv[:1])

    from src.core.models import Project
    from src.core.project_manager import ProjectManager
    from src.network.network_manager import NetworkManager
    from src.engine.playback_engine import PlaybackEngine

    project = Project.load(project_json_path)
    pm = ProjectManager()
    pm._project = project
    nm = NetworkManager()
    nm._running = True
    nm._send_to_hw = lambda *a, **k: None
    engine = PlaybackEngine(nm, pm)

    # 预编译（本 worker 只需其区间会用到的 clip，但保守起见全量预编译，与串行等价）
    engine._precompile_dirty = True
    engine._precompile_signature = None
    engine.precompile_project_clips(project)

    # 从 start-LEAD_IN 起渲染以暖机跨帧累积状态,再截取自己的区间 → 与串行逐字节一致。
    warm_start = max(0, int(start_idx) - LEAD_IN_FRAMES)
    frame_cache, record_cache = engine._render_timeline_frame_range(
        project, warm_start, int(stop_idx),
        float(interval), float(end), int(frame_count),
        progress_callback=None,
    )
    # 丢弃预热帧,只保留 [start_idx, stop_idx)
    if warm_start < int(start_idx):
        frame_cache = {i: v for i, v in frame_cache.items() if i >= int(start_idx)}
        record_cache = {i: v for i, v in record_cache.items() if i >= int(start_idx)}

    with open(out_path, "wb") as f:
        pickle.dump({"frame": frame_cache, "record": record_cache}, f,
                    protocol=pickle.HIGHEST_PROTOCOL)
    return (out_path, int(start_idx), len(frame_cache))


def run_parallel_render(
    project_to_dict: Dict[str, Any],
    interval: float,
    end: float,
    frame_count: int,
    repo_root: str,
    progress_callback: Optional[Callable] = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    n_workers: Optional[int] = None,
) -> Optional[Tuple[Dict[int, dict], Dict[int, dict]]]:
    """多进程渲染整条时间线帧缓存。返回 (frame_cache, record_source_cache) 或 None(不可用)。

    None = 帧数太少/worker 数=1/任一步失败 → 调用方回退串行。
    """
    if frame_count <= 0:
        return None
    if n_workers is None:
        n_workers = _default_worker_count(frame_count)
    if n_workers <= 1:
        return None   # 单 worker 无意义，回退串行

    ranges = _chunk_ranges(frame_count, n_workers)
    if len(ranges) <= 1:
        return None

    tmp_dir = tempfile.mkdtemp(prefix="qascade_prender_")
    proj_json = os.path.join(tmp_dir, "project.dmxt")
    out_paths: List[str] = []
    try:
        import json
        with open(proj_json, "w", encoding="utf-8") as f:
            json.dump(project_to_dict, f, ensure_ascii=False)

        tasks = []
        for i, (s, e) in enumerate(ranges):
            op = os.path.join(tmp_dir, f"part_{i:03d}.pkl")
            out_paths.append(op)
            tasks.append((proj_json, s, e, interval, end, frame_count, op, repo_root))

        # spawn 上下文：跨平台一致，且不继承父进程 Qt 状态
        ctx = mp.get_context("spawn")
        frame_cache: Dict[int, dict] = {}
        record_cache: Dict[int, dict] = {}
        done_frames = 0
        with ctx.Pool(processes=len(ranges)) as pool:
            for (op, start_idx, n_frames) in pool.imap_unordered(_worker_render_range, tasks):
                with open(op, "rb") as f:
                    part = pickle.load(f)
                frame_cache.update(part["frame"])
                record_cache.update(part["record"])
                done_frames += n_frames
                if progress_callback is not None:
                    frac = float(done_frames) / max(1.0, float(frame_count))
                    overall = float(progress_start) + (
                        max(0.0, float(progress_end) - float(progress_start)) * frac
                    )
                    try:
                        progress_callback({
                            "phase": "render",
                            "current": done_frames,
                            "total": frame_count,
                            "message": f"并行渲染帧缓存 {done_frames}/{frame_count}",
                            "percent": overall,
                        })
                    except Exception:
                        pass
        return frame_cache, record_cache
    finally:
        # 清理临时文件
        for op in out_paths:
            try:
                if os.path.exists(op):
                    os.remove(op)
            except Exception:
                pass
        try:
            if os.path.exists(proj_json):
                os.remove(proj_json)
            os.rmdir(tmp_dir)
        except Exception:
            pass
