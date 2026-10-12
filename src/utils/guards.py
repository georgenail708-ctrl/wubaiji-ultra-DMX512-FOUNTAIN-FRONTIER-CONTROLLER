"""
src/utils/guards.py — 防御编程工具集
基于 Trail of Bits (安全公司) + Sting25 scaffold 防御模式
提供：断言助手、不变量检查、结构化日志、硬件安全防护。

用法:
    from src.utils.guards import invariant, require, clamp_dmx, log_context

    # 不变量检查（debug 模式下断言，release 下警告）
    invariant(len(dmx_buffer) == 512, "DMX buffer must be 512 channels")

    # 前置条件检查（始终执行，抛出 ValueError）
    require(0 <= channel < 512, f"channel {channel} out of range [0,512)")

    # DMX 数值钳制
    value = clamp_dmx(300)  # → 255

    # 结构化日志上下文
    with log_context("DMX tick", extra={"universe": 1}):
        engine.tick()
"""

from __future__ import annotations

import functools
import logging
import os
import sys
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Optional, TypeVar

import numpy as np

_log = logging.getLogger("Qascade.Guards")

# ── 环境感知模式 ──────────────────────────────────────────────────
IS_DEV = not getattr(sys, "frozen", False)
IS_PROD = getattr(sys, "frozen", False)

T = TypeVar("T")


# ── 断言 / 不变量 ─────────────────────────────────────────────────

def invariant(condition: bool, message: str = "invariant violated") -> None:
    """不变量检查 — dev 下 assert 阻断，prod 下 log 警告。

    来源: Trail of Bits 'fail fast' 原则 + Linus 'show me the code'
    """
    if condition:
        return
    if IS_DEV:
        raise AssertionError(f"[INVARIANT] {message}")
    else:
        _log.warning("[INVARIANT] %s (prod: non-fatal)", message)
        _log.debug("Stack trace:\n%s", "".join(traceback.format_stack()[:-1]))


def require(condition: bool, message: str = "precondition failed") -> None:
    """前置条件检查 — 所有模式均 raise ValueError。

    来源: Trail of Bits 'validate at boundaries' 模式
    """
    if not condition:
        raise ValueError(f"[PRECONDITION] {message}")


def ensure(condition: bool, message: str = "postcondition failed") -> None:
    """后置条件检查 — dev 下 assert，prod 下 log 警告。"""
    if condition:
        return
    if IS_DEV:
        raise AssertionError(f"[POSTCONDITION] {message}")
    else:
        _log.error("[POSTCONDITION] %s", message)


# ── DMX 专用防护 ──────────────────────────────────────────────────

def clamp_dmx(value: int) -> int:
    """将数值钳制到 DMX 有效范围 [0, 255]。"""
    if 0 <= value <= 255:
        return value
    _log.debug("clamp_dmx: %d → %d", value, max(0, min(255, value)))
    return max(0, min(255, value))


def clamp_dmx_16bit(value: int) -> int:
    """将数值钳制到 DMX 16-bit 范围 [0, 65535]。"""
    if 0 <= value <= 65535:
        return value
    _log.debug("clamp_dmx_16bit: %d → %d", value, max(0, min(65535, value)))
    return max(0, min(65535, value))


def validate_dmx_buffer(buf: "np.ndarray", universe: int = 0) -> "np.ndarray":
    """验证 DMX 缓冲区为 uint8 且长度 ≥ 512。不合规时尝试修正。"""
    if buf.dtype != np.uint8:
        _log.warning("DMX buffer dtype %s → uint8 (universe=%d)", buf.dtype, universe)
        buf = buf.astype(np.uint8)

    if len(buf) < 512:
        _log.error("DMX buffer len=%d < 512 (universe=%d) — padding with zeros", len(buf), universe)
        padded = np.zeros(512, dtype=np.uint8)
        padded[: len(buf)] = buf
        buf = padded

    return buf


def validate_array_ndim(arr: "np.ndarray", expected_ndim: int, name: str = "array") -> None:
    """验证 numpy 数组维数。"""
    actual = arr.ndim
    invariant(
        actual == expected_ndim,
        f"{name}.ndim={actual} expected={expected_ndim}",
    )


# ── 结构化日志 ────────────────────────────────────────────────────

@contextmanager
def log_context(
    operation: str,
    *,
    extra: Optional[dict] = None,
    level: int = logging.DEBUG,
    warn_slow_ms: float = 16.0,  # 超过 1 帧 @60fps 警告
):
    """结构化日志上下文管理器 — 自动记录进入/退出/耗时/慢操作。

    来源: Trail of Bits 'fail fast with clear messages' + HermeticOrmus 'structlog' 模式

    Usage:
        with log_context("render_frame", extra={"frame": 42}):
            render()
    """
    extra = extra or {}
    t0 = time.perf_counter_ns()
    _log.log(level, "[+] %s started %s", operation, extra or "")

    try:
        yield
    except Exception:
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000
        _log.error("[!] %s FAILED after %.1fms %s", operation, elapsed_ms, extra or "")
        raise
    else:
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000
        if elapsed_ms > warn_slow_ms:
            _log.warning("[*] %s SLOW: %.1fms (threshold=%.1fms) %s",
                         operation, elapsed_ms, warn_slow_ms, extra or "")
        else:
            _log.log(level, "[-] %s done in %.2fms %s", operation, elapsed_ms, extra or "")


# ── 重试与降级 ────────────────────────────────────────────────────

def retry_on_failure(
    max_retries: int = 3,
    backoff_s: float = 0.1,
    exceptions: tuple = (Exception,),
    log_level: int = logging.WARNING,
):
    """装饰器: 失败时自动重试，指数退避。

    来源: Trail of Bits 'fail fast' + darrenoakey 'catch to add context + re-raise'

    Usage:
        @retry_on_failure(max_retries=3, exceptions=(OSError,))
        def send_dmx_packet(data):
            ...
    """
    def _decorator(fn: Callable):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        delay = backoff_s * (2 ** attempt)
                        _log.log(
                            log_level,
                            "[retry] %s attempt %d/%d failed: %s — retrying in %.2fs",
                            fn.__qualname__, attempt + 1, max_retries, e, delay,
                        )
                        time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return _wrapper

    return _decorator


# ── 硬件/环境防护 ─────────────────────────────────────────────────

def ensure_output_directory(path: str) -> str:
    """确保输出目录存在（原子操作，线程安全）。

    来源: darrenoakey 'no side effects from missing dirs'
    """
    import os
    os.makedirs(path, exist_ok=True)
    return path


def safe_tempfile(prefix: str = "qascade_", suffix: str = ".tmp") -> str:
    """创建临时文件路径（不创建实际文件，线程安全）。"""
    import tempfile
    import uuid
    return os.path.join(
        tempfile.gettempdir(),
        f"{prefix}{uuid.uuid4().hex[:8]}{suffix}",
    )


# ── 数值安全 ──────────────────────────────────────────────────────

def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """安全除法 — 除零返回 default。"""
    return numerator / denominator if abs(denominator) > 1e-15 else default


def is_valid_float(value: Any) -> bool:
    """检查值是否为有效浮点数（非 NaN，非 Inf）。"""
    if not isinstance(value, (int, float, np.floating)):
        return False
    return np.isfinite(float(value))


def sanitize_numpy_array(arr: "np.ndarray") -> "np.ndarray":
    """替换数组中的 NaN/Inf 为 0，返回副本。"""
    if not np.isfinite(arr).all():
        _log.warning("sanitize_numpy_array: found NaN/Inf in array shape=%s", arr.shape)
        return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0).copy()
    return arr
