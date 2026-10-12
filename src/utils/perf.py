"""
src/utils/perf.py — 性能分析工具集
整合 Scalene (12k★) + PyInstrument (7k★) + py-spy (13k★)
为 Qascade DMX 实时控制提供零侵入/低开销性能诊断。

用法:
    from src.utils.perf import profile_region, benchmark, render_hotpath_report

    # 装饰器：对单个函数做 PyInstrument 采样
    @profile_region
    def hot_function():
        ...

    # 上下文管理器：测量代码块耗时
    with benchmark("DMX tick loop"):
        engine.tick()

    # 生成渲染管道性能报告
    render_hotpath_report(engine, output_path="perf_report.html")
"""

from __future__ import annotations

import contextlib
import functools
import os
import threading
import time
from typing import Callable, Optional

# ── 延迟导入，避免冷启动开销 ──────────────────────────────────────

_scalene_available: Optional[bool] = None
_pyinstrument_available: Optional[bool] = None


def _has_scalene() -> bool:
    global _scalene_available
    if _scalene_available is None:
        try:
            import scalene  # noqa: F401
            _scalene_available = True
        except ImportError:
            _scalene_available = False
    return _scalene_available


def _has_pyinstrument() -> bool:
    global _pyinstrument_available
    if _pyinstrument_available is None:
        try:
            import pyinstrument  # noqa: F401
            _pyinstrument_available = True
        except ImportError:
            _pyinstrument_available = False
    return _pyinstrument_available


# ── 低开销计时器（ns 精度，适合 DMX tick 热路径）─────────────────

class TickTimer:
    """DMX tick 循环专用计时器 — 预分配 ring buffer，零分配开销。"""

    __slots__ = ("_times_ns", "_cursor", "_count", "_capacity", "_start_ns")

    def __init__(self, capacity: int = 1024) -> None:
        import numpy as np
        self._times_ns: "np.ndarray" = np.zeros(capacity, dtype=np.int64)
        self._cursor: int = 0
        self._count: int = 0
        self._capacity: int = capacity
        self._start_ns: int = 0

    def start(self) -> None:
        self._start_ns = time.perf_counter_ns()

    def tick(self) -> int:
        """记录一次 tick 耗时（纳秒），返回距上次 tick 的 ns 差值。"""
        now = time.perf_counter_ns()
        elapsed = now - self._start_ns
        self._times_ns[self._cursor] = elapsed
        self._cursor = (self._cursor + 1) % self._capacity
        if self._count < self._capacity:
            self._count += 1
        self._start_ns = now
        return elapsed

    def stats(self) -> dict:
        """返回 tick 统计：平均/中位数/P99/最大/最小（毫秒）。"""
        import numpy as np
        if self._count == 0:
            return {"avg_ms": 0.0, "p50_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0, "min_ms": 0.0}
        if self._count < self._capacity:
            valid = self._times_ns[: self._count] / 1_000_000  # ns → ms
        else:
            # 环形缓冲已回绕：最旧值在 _cursor 处，按 cursor 拼接切片恢复时间序
            valid = np.concatenate(
                (self._times_ns[self._cursor:], self._times_ns[: self._cursor])
            ) / 1_000_000
        return {
            "avg_ms": float(np.mean(valid)),
            "p50_ms": float(np.percentile(valid, 50)),
            "p99_ms": float(np.percentile(valid, 99)),
            "max_ms": float(np.max(valid)),
            "min_ms": float(np.min(valid)),
            "count": self._count,
        }


# ── PyInstrument 装饰器 ───────────────────────────────────────────

def profile_region(func: Callable = None, *, interval: float = 0.001):
    """PyInstrument 采样分析装饰器 — 仅当环境变量 QASCADE_PROFILE=1 时激活。"""
    def _decorator(fn: Callable):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            if os.getenv("QASCADE_PROFILE") != "1" or not _has_pyinstrument():
                return fn(*args, **kwargs)
            import pyinstrument
            profiler = pyinstrument.Profiler(interval=interval)
            profiler.start()
            try:
                return fn(*args, **kwargs)
            finally:
                profiler.stop()
                # 输出到 stderr 避免污染 DMX 输出日志
                import sys
                print(f"\n{'=' * 60}", file=sys.stderr)
                print(f"Profile: {fn.__qualname__}", file=sys.stderr)
                print(f"{'=' * 60}", file=sys.stderr)
                print(profiler.output_text(unicode=True, color=True), file=sys.stderr)

        return _wrapper
    if func is not None:
        return _decorator(func)
    return _decorator


# ── 低开销基准测试 ────────────────────────────────────────────────

@contextlib.contextmanager
def benchmark(label: str = "", *, warmup: int = 1, runs: int = 10, silent: bool = False):
    """测量代码块性能的上下文管理器。

    Args:
        label: 标签（用于输出）
        warmup: 预热次数（不计入统计）
        runs: 正式测量次数
        silent: True 时不打印，返回值在 context.result 中

    Yields:
        上下文对象，结束后 .result 包含统计信息
    """
    class _Ctx:
        result: Optional[dict] = None

    ctx = _Ctx()

    # 预热
    for _ in range(warmup):
        yield ctx

    # 正式测量
    times_s = []
    for _ in range(runs):
        t0 = time.perf_counter_ns()
        try:
            yield ctx
        finally:
            elapsed_ns = time.perf_counter_ns() - t0
            times_s.append(elapsed_ns / 1_000_000_000.0)

    import numpy as np
    arr = np.array(times_s)
    ctx.result = {
        "label": label,
        "runs": runs,
        "avg_s": float(np.mean(arr)),
        "min_s": float(np.min(arr)),
        "max_s": float(np.max(arr)),
        "p99_s": float(np.percentile(arr, 99)),
    }

    if not silent and label:
        import sys
        print(
            f"[perf] {label}: avg={ctx.result['avg_s']*1000:.2f}ms "
            f"min={ctx.result['min_s']*1000:.2f}ms "
            f"p99={ctx.result['p99_s']*1000:.2f}ms "
            f"(n={runs})",
            file=sys.stderr,
        )


# ── 渲染管道热点报告 ──────────────────────────────────────────────

def render_hotpath_report(
    engine=None, *, output_path: str = "perf_report.html", duration_s: float = 10.0
) -> str:
    """生成渲染管道性能报告（需要 Scalene 或 PyInstrument）。

    对引擎的渲染循环采样 duration_s 秒，输出 HTML 火焰图报告。

    Args:
        engine: PlaybackEngine 实例
        output_path: 输出 HTML 报告路径
        duration_s: 采样时长（秒）

    Returns:
        报告文件路径
    """
    if _has_scalene():
        return _render_scalene_report(engine, output_path, duration_s)
    if _has_pyinstrument():
        return _render_pyinstrument_report(engine, output_path, duration_s)
    raise RuntimeError("No profiler available. Install scalene or pyinstrument.")


def _render_scalene_report(engine, output_path: str, duration_s: float) -> str:
    import scalene

    scalene_profiler = scalene.Scalene()
    scalene_profiler.start()

    deadline = time.perf_counter() + duration_s
    while time.perf_counter() < deadline:
        if engine and hasattr(engine, "tick"):
            engine.tick()
        time.sleep(0.001)  # 1ms 粒度

    scalene_profiler.stop()

    # Scalene 自动输出 HTML 报告
    profile_path = output_path.replace(".html", "") if output_path.endswith(".html") else output_path
    scalene_profiler.output_html(profile_path)  # type: ignore[attr-defined]
    return output_path


def _render_pyinstrument_report(engine, output_path: str, duration_s: float) -> str:
    import pyinstrument

    profiler = pyinstrument.Profiler(interval=0.0005)  # 0.5ms 采样间隔
    profiler.start()

    deadline = time.perf_counter() + duration_s
    while time.perf_counter() < deadline:
        if engine and hasattr(engine, "tick"):
            engine.tick()
        time.sleep(0.001)

    profiler.stop()
    html = profiler.output_html()
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


# ── 线程安全检测装饰器 ────────────────────────────────────────────

_THREAD_LOCKS: dict = {}

def threadsafe(key: str = ""):
    """标记函数需要线程安全保护（为 DMX tick + PyQt 主线程设计）。

    使用可重入锁，同一线程可嵌套调用。

    Args:
        key: 锁名（同一 key 共享锁，空字符串 = 函数名）
    """
    def _decorator(fn: Callable):
        lock_key = key or fn.__qualname__

        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            if lock_key not in _THREAD_LOCKS:
                _THREAD_LOCKS[lock_key] = threading.RLock()
            with _THREAD_LOCKS[lock_key]:
                return fn(*args, **kwargs)

        return _wrapper

    return _decorator
