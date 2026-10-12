# -*- coding: utf-8 -*-
"""Qascade CE 统一调度器 — 探测 → 路由 → 分发 → 遥测（蓝图 §4.1 + §8.4 契约）。

阶段E 交付（render_lab/COMPUTE_UTILIZATION_PLAN.md §4.1 / §8.2 / §8.4 / §8.5）:
  detect_backend()     §4.1 探测链 + 结果缓存 + 环境覆写
  register_kernel()    内核能力表注册（单点增删; 缺实现 = 自动 CPU 回退）
  schedule()           §8.4 契约签名: 路由 + 执行 + 回退 + 遥测
  stats()              per-stage 耗时 / 回退计数（QASCADE_CE_PROF=1 时含
                       GIL 持有采样）

§8.2 首期 5 内核能力表种子在 kernel_seed.seed_kernel_table()（独立文件,
保持各文件 ≤500 行; 注册入口 register_kernel 在本模块）。

契约与纪律:
  - GPU 默认关闭: QASCADE_CE_GPU 未设或 0 → 永不启用 GPU（§8.6: 哈希
    73d9e1c2 不漂）; 1/auto = 自动探测链; cuda/opencl = 强制（不可用 → CPU
    回退, 不假死）。
  - 缺失符号/实现 = 回退非假死（同 ctypes_api._bind 约定）; 路由日志每
    (内核, 后端) 一次, 不刷 tick。
  - 错误码: 0=成功, 负值=错误（-1 通用, -2 GPU 设备丢失, -3 GPU 编译失败）。
  - 任务量 < 512 恒走 CPU（§4.4: GPU 启动延迟 ~100µs > 收益; 小任务合批）。
  - 符号前缀: ce_sched_*（C 侧; Python 侧模块函数名 = §8.4 冻结签名）。
  - 后端实现统一签名 fn(args: dict) -> int; args 为关键字参数字典, 输出缓冲
    由调用方预分配（同 ctypes_api 纪律）。
"""
from __future__ import annotations

import ctypes
import logging
import os
import time

import numpy as np

from .ctypes_api import load_engine

log = logging.getLogger(__name__)

# ── 能力位（镜像 csrc/ce_gpu_api.h, 位级等价保证等级）──────────────────
CE_GPU_CAP_EXACT_MIX = 1 << 0    # 帧合成与 ce_mix_clips 位级一致（纯 IEEE）
CE_GPU_CAP_EXACT_WAVE = 1 << 1   # 波形与 ce_wave_effect 位级一致（GPU 恒 0:
                                 # 超越函数 sin/cos/exp 与主机 libm 最后 1 ulp
                                 # 不保证 → wave 族注册 bit_exact=False）

# §4.4: GPU 启动延迟 ~100µs 固定成本 → 任务量下限（< 512 恒走 CPU 合批）
_GPU_MIN_TASKS = 512

# ce_gpu_api.h: CE_GPU_BACKEND_CUDA=1 / CE_GPU_BACKEND_OPENCL=2
_GPU_BACKEND_ID = {"cuda": 1, "opencl": 2}
_GPU_DLL_NAME = {"cuda": "ce_gpu_cuda.dll", "opencl": "ce_gpu_opencl.dll"}
_GPU_ORDER_ALL = (("ce_gpu_cuda.dll", "cuda"), ("ce_gpu_opencl.dll", "opencl"))
_GPU_ORDER_FORCED = {"cuda": (("ce_gpu_cuda.dll", "cuda"),),
                     "opencl": (("ce_gpu_opencl.dll", "opencl"),)}

_REGISTRY: dict = {}            # name → _KernelEntry
_TEL = {"kernels": {}, "gpu_init_fail": 0, "unknown_kernels": 0,
        "last_error": None, "stages": {}}
_CACHE = {"backend": None, "threads": None}
_gpu_dev = None                  # 惰性初始化的持久 GPU 句柄（首次 GPU 路由时建）
_gpu_init_done = False           # 本会话仅尝试一次（失败不再重试, 同 _installed 纪律）
_NOTE_DONE = set()               # (name, backend) 一次性路由日志


class _KernelEntry:
    """能力表条目: 各后端实现 + 位级等价要求。"""

    __slots__ = ("cpu_fn", "opencl", "cuda", "bit_exact")

    def __init__(self, cpu_fn, opencl, cuda, bit_exact):
        self.cpu_fn = cpu_fn
        self.opencl = opencl
        self.cuda = cuda
        self.bit_exact = bit_exact


def _parse_threads() -> int:
    """QASCADE_CE_THREADS=1..64 工作池线程数; 默认 = os.cpu_count(); 非法 → 默认。"""
    raw = (os.environ.get("QASCADE_CE_THREADS", "") or "").strip()
    if not raw:
        return max(1, os.cpu_count() or 1)
    try:
        n = int(raw)
    except ValueError:
        log.warning("[CE] QASCADE_CE_THREADS=%r 非整数 → 默认", raw)
        return max(1, os.cpu_count() or 1)
    if n < 1:
        log.warning("[CE] QASCADE_CE_THREADS=%d < 1 → 1", n)
        return 1
    if n > 64:
        log.warning("[CE] QASCADE_CE_THREADS=%d > 64 → 64", n)
        return 64
    return n


def _probe_gpu_chain(order) -> str | None:
    """按序探测 GPU DLL: ce_gpu_probe() 判本机后端, init 成功才认定可用。

    链序（§4.1）: ce_gpu_cuda.dll（NVIDIA→cuda）→ ce_gpu_opencl.dll
    （OpenCL vendor AMD/NVIDIA → opencl）; 任一失败继续下一项。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for dll_name, tag in order:
        path = os.path.join(here, dll_name)
        if not os.path.exists(path):
            continue
        try:
            dll = ctypes.CDLL(path)
            dll.ce_gpu_probe.restype = ctypes.c_int
            if dll.ce_gpu_probe() == 0:
                continue
            dll.ce_gpu_init.restype = ctypes.c_void_p
            dll.ce_gpu_init.argtypes = [ctypes.c_int, ctypes.c_char_p]
            dll.ce_gpu_destroy.argtypes = [ctypes.c_void_p]
            dev = dll.ce_gpu_init(_GPU_BACKEND_ID[tag], None)
            if dev is None:
                continue
            dll.ce_gpu_destroy(dev)
            return tag
        except Exception as e:  # noqa: BLE001 — 探测异常继续链上下一项
            log.warning("[CE] GPU 探测 %s 异常: %s", dll_name, e)
    return None


def detect_backend(force: bool = False) -> str:
    """§4.1 探测链, 结果缓存（force=True 强制重探测; 探针用）。

    链序: nvcuda→cuda → OpenCL(vendor AMD/NVIDIA)→opencl → ce_engine
    avx2/sse2 → cpu-* → 全失败 → python。
    环境覆写: QASCADE_CE_GPU=0(默认=永不启用)|1|auto|cuda|opencl;
    QASCADE_CE_THREADS=1..64（工作池线程数, 默认=os.cpu_count()）。
    返回: "cuda" | "opencl" | "cpu-avx2" | "cpu-sse2" | "python"。
    """
    if not force and _CACHE["backend"] is not None:
        return _CACHE["backend"]
    threads = _parse_threads()
    gpu = (os.environ.get("QASCADE_CE_GPU", "0") or "0").strip().lower()
    backend = "python"
    if gpu not in ("0", ""):
        order = (_GPU_ORDER_ALL if gpu in ("1", "auto")
                 else _GPU_ORDER_FORCED.get(gpu, ()))
        tag = _probe_gpu_chain(order) if order else None
        if tag is not None:
            backend = tag
    if backend == "python":
        engine = load_engine()
        if engine is not None:
            backend = f"cpu-{engine.variant}"
    _CACHE["backend"] = backend
    _CACHE["threads"] = threads
    log.info("[CE] detect_backend=%s threads=%d gpu_env=%r", backend, threads, gpu)
    return backend


class _GpuDevice:
    """已初始化的 GPU 后端句柄（ce_gpu_api.h 契约, ctypes 直调, 不依赖 pyopencl）。

    线程安全: 单句柄不支持并发 run（头文件声明）→ 调用方须串行化
    （DMX tick 单线程; 多线程使用需每线程句柄或外部锁）。
    """

    def __init__(self, backend: str, dll: ctypes.CDLL, dev: int):
        self.backend = backend
        self._dll = dll
        self._dev = dev
        self.name = self._query_str("ce_gpu_name")
        self.caps = self._query_int("ce_gpu_caps")
        self._err_fn = getattr(dll, "ce_gpu_last_error", None)

    @staticmethod
    def open(backend: str) -> _GpuDevice | None:
        """按后端加载 DLL + init; 失败返回 None（调用方回退 CPU, 不假死）。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            _GPU_DLL_NAME[backend])
        if not os.path.exists(path):
            return None
        try:
            dll = ctypes.CDLL(path)
            dll.ce_gpu_init.restype = ctypes.c_void_p
            dll.ce_gpu_init.argtypes = [ctypes.c_int, ctypes.c_char_p]
            dev = dll.ce_gpu_init(_GPU_BACKEND_ID[backend], None)
        except Exception as e:  # noqa: BLE001 — 加载失败回退 CPU
            log.warning("[CE] GPU %s 加载失败: %s", backend, e)
            return None
        if dev is None:
            return None
        return _GpuDevice(backend, dll, dev)

    def _query_str(self, fn_name: str) -> str:
        f = getattr(self._dll, fn_name, None)
        if f is None:
            return ""
        f.restype = ctypes.c_char_p
        f.argtypes = [ctypes.c_void_p]
        try:
            s = f(self._dev)
            return s.decode("utf-8", "replace") if s else ""
        except Exception:  # noqa: BLE001 — 查询失败回空串
            return ""

    def _query_int(self, fn_name: str) -> int:
        f = getattr(self._dll, fn_name, None)
        if f is None:
            return 0
        f.restype = ctypes.c_uint32
        f.argtypes = [ctypes.c_void_p]
        try:
            return int(f(self._dev))
        except Exception:  # noqa: BLE001 — 查询失败回 0
            return 0

    def last_error(self) -> str:
        if self._err_fn is None:
            return ""
        self._err_fn.restype = ctypes.c_char_p
        self._err_fn.argtypes = [ctypes.c_void_p]
        try:
            s = self._err_fn(self._dev)
            return s.decode("utf-8", "replace") if s else ""
        except Exception:  # noqa: BLE001 — 查询失败回空串
            return ""

    # ── ce_gpu_mix_batch: K0 批量帧合成（位级等价 ce_mix_clips）─────────
    def mix_batch(self, args: dict) -> int:
        """args = "ce_gpu_mix_clips" 的 dict（vals/acts/anchors [U][lmax][512],
        nlays [U], lmax, mod_gain, out/act_any [U][512]）。返回 0 / -1。"""
        f = getattr(self._dll, "ce_gpu_mix_batch", None)
        if f is None:
            return -1
        _F = ctypes.POINTER(ctypes.c_float)
        _U = ctypes.POINTER(ctypes.c_uint8)
        _I = ctypes.POINTER(ctypes.c_int)
        f.restype = ctypes.c_int
        f.argtypes = [ctypes.c_void_p, _F, _U, _F, _I,
                      ctypes.c_int, ctypes.c_int, ctypes.c_float, _U, _U]
        nlays = np.ascontiguousarray(args["nlays"], np.int32)
        return int(f(self._dev,
                     np.ascontiguousarray(args["vals"], np.float32).ctypes.data_as(_F),
                     np.ascontiguousarray(args["acts"], np.uint8).ctypes.data_as(_U),
                     np.ascontiguousarray(args["anchors"], np.float32).ctypes.data_as(_F),
                     nlays.ctypes.data_as(_I), int(nlays.shape[0]),
                     int(args["lmax"]), float(args["mod_gain"]),
                     np.ascontiguousarray(args["out"], np.uint8).ctypes.data_as(_U),
                     np.ascontiguousarray(args["act_any"], np.uint8).ctypes.data_as(_U)))

    # ── ce_gpu_wave_batch: K1 批量波形求值（对齐 ce_wave_effect）─────────
    def wave_batch(self, args: dict) -> int:
        """args = "ce_gpu_wave" 的 dict（单任务: t/speed/phase_step/lo/hi/
        wave_fn/n + out[n]）。返回 0 / -1。"""
        f = getattr(self._dll, "ce_gpu_wave_batch", None)
        if f is None:
            return -1
        _D = ctypes.POINTER(ctypes.c_double)
        _I = ctypes.POINTER(ctypes.c_int)
        _U = ctypes.POINTER(ctypes.c_uint8)
        f.restype = ctypes.c_int
        f.argtypes = [ctypes.c_void_p, _D, _D, _D, _D, _D, _I, _I, _I,
                      ctypes.c_int, _U]
        n = int(args["n"])
        ones = lambda v: np.asarray([float(v)], np.float64)  # noqa: E731
        offs = np.asarray([0, n], np.int32)
        return int(f(self._dev,
                     ones(args["t"]).ctypes.data_as(_D),
                     ones(args["speed"]).ctypes.data_as(_D),
                     ones(args["phase_step"]).ctypes.data_as(_D),
                     ones(args["lo"]).ctypes.data_as(_D),
                     ones(args["hi"]).ctypes.data_as(_D),
                     np.asarray([int(args["wave_fn"])], np.int32).ctypes.data_as(_I),
                     np.asarray([n], np.int32).ctypes.data_as(_I),
                     offs.ctypes.data_as(_I), 1,
                     np.ascontiguousarray(args["out"], np.uint8).ctypes.data_as(_U)))


def register_kernel(name, cpu_fn=None, opencl=None, cuda=None, bit_exact=True):
    """内核能力表注册（单点增删; 重复注册 = 覆盖, 最新生效）。

    cpu_fn/opencl/cuda: 各后端实现, 统一签名 fn(args: dict) -> int
    （0 成功 / 负值错误; 输出缓冲由调用方预分配, 同 ctypes_api 纪律）。
    bit_exact: True = GPU 路径要求设备位级能力位（CE_GPU_CAP_EXACT_MIX/
    WAVE, ce_gpu_api.h）; 设备缺位 → 恒走 CPU（门1 哈希不漂）。
    未提供的实现 = 该后端自动回退 CPU（永不静默错路）。
    """
    _REGISTRY[name] = _KernelEntry(cpu_fn, opencl, cuda, bool(bit_exact))
    _TEL["kernels"].setdefault(name, {"calls": 0, "cpu": 0, "gpu": 0,
                                      "fallback": 0, "gpu_denied": 0,
                                      "errors": 0})
    log.debug("[CE] register_kernel %s bit_exact=%s", name, bit_exact)


def _note_gpu_missing(name: str, backend: str) -> None:
    key = (name, backend)
    if key in _NOTE_DONE:
        return
    _NOTE_DONE.add(key)
    log.info("[CE] schedule %s: %s 无实现 → 恒 CPU（永不静默错路）", name, backend)


def _ensure_gpu_dev(backend: str) -> _GpuDevice | None:
    """惰性初始化持久 GPU 句柄（首次 GPU 路由时; 失败 → 计数 + 本会话不再重试）。"""
    global _gpu_dev, _gpu_init_done
    if _gpu_init_done:
        return _gpu_dev
    _gpu_init_done = True
    _gpu_dev = _GpuDevice.open(backend)
    if _gpu_dev is None:
        _TEL["gpu_init_fail"] += 1
        log.warning("[CE] GPU %s 初始化失败 → 路由 CPU（遥测计数 %d）",
                    backend, _TEL["gpu_init_fail"])
    else:
        log.info("[CE] GPU 设备: %s (%s) caps=0x%x", _gpu_dev.name,
                 _gpu_dev.backend, _gpu_dev.caps)
    return _gpu_dev


def _route_gpu(entry, name, tel, backend, n_tasks) -> bool:
    """§4.1 路由规则: GPU 仅当 后端=GPU 且 任务量≥512 且 有实现 且 位级能力满足。"""
    if backend not in ("cuda", "opencl"):
        return False
    if n_tasks < _GPU_MIN_TASKS:
        return False
    fn = entry.opencl if backend == "opencl" else entry.cuda
    if fn is None:
        _note_gpu_missing(name, backend)
        tel["gpu_denied"] += 1
        return False
    dev = _ensure_gpu_dev(backend)
    if dev is None:
        tel["gpu_denied"] += 1
        return False
    if entry.bit_exact and not (dev.caps & (CE_GPU_CAP_EXACT_MIX
                                            | CE_GPU_CAP_EXACT_WAVE)):
        tel["gpu_denied"] += 1
        return False
    return True


def _prof_on() -> bool:
    return os.environ.get("QASCADE_CE_PROF", "0") == "1"


def _stage_tick(name: str, backend: str) -> None:
    """per-stage 遥测（QASCADE_CE_PROF=1）: wall + GIL 持有采样。

    GIL 持有时间代理 = time.thread_time()（当前线程 CPU 时间）: ctypes 调用
    自动释放 GIL 时线程 CPU 不增长 → 差值 ≈ GIL 持有时长（§6.1.4 断言 <1ms）。
    """
    wall = (time.perf_counter() - _TICK_T0) * 1e3
    gil = (time.thread_time() - _TICK_CPU0) * 1e3
    st = _TEL["stages"].setdefault(name, {}).setdefault(backend, {
        "calls": 0, "wall_ms": 0.0, "gil_ms": 0.0, "max_wall_ms": 0.0})
    st["calls"] += 1
    st["wall_ms"] += wall
    st["gil_ms"] += gil
    st["max_wall_ms"] = max(st["max_wall_ms"], wall)


_TICK_T0 = 0.0
_TICK_CPU0 = 0.0


def schedule(name, args, n_tasks, stream=0):
    """§8.4 统一入口: 路由 + 执行 + 回退 + 遥测。

    args: 关键字参数字典, 原样传给所选后端实现（输出缓冲由调用方预分配）。
    n_tasks: 任务量（域/设备数）; < 512 恒走 CPU（§4.4 合批）。
    stream: GPU 流编号（0=默认）; 当前实现的内核（ce_gpu_mix_batch /
    ce_gpu_wave_batch）为同步单流, stream 保留供后续异步内核。
    返回: 0 成功 / 负值错误（-1 通用, -2 GPU 设备丢失, -3 GPU 编译失败）;
    CPU 实现抛异常 → 原样上抛（调用方 try/except 回退原路径, 同既有 hook 纪律）。
    """
    global _TICK_T0, _TICK_CPU0
    entry = _REGISTRY.get(name)
    if entry is None:
        _TEL["unknown_kernels"] += 1
        log.error("[CE] schedule 未注册内核 %r", name)
        return -1
    tel = _TEL["kernels"][name]
    tel["calls"] += 1
    prof = _prof_on()
    backend = detect_backend()
    if _route_gpu(entry, name, tel, backend, n_tasks):
        fn = entry.opencl if _gpu_dev.backend == "opencl" else entry.cuda
        if prof:
            _TICK_T0 = time.perf_counter()
            _TICK_CPU0 = time.thread_time()
        try:
            res = fn(args)
            rc = int(res) if res is not None else 0
        except Exception as e:  # noqa: BLE001 — GPU 异常 → 同帧回退 CPU
            rc = -2
            _TEL["last_error"] = f"GPU {type(e).__name__}: {e}"
        if prof:
            _stage_tick(name, "gpu")
        if rc == 0:
            tel["gpu"] += 1
            return 0
        tel["fallback"] += 1
        log.warning("[CE] schedule %s GPU 失败 rc=%d → 同帧回退 CPU", name, rc)
    if entry.cpu_fn is None:
        tel["errors"] += 1
        log.error("[CE] schedule %s 无 CPU 实现", name)
        return -1
    if prof:
        _TICK_T0 = time.perf_counter()
        _TICK_CPU0 = time.thread_time()
    try:
        rc = entry.cpu_fn(args)
    except Exception:
        tel["errors"] += 1
        raise
    if prof:
        _stage_tick(name, "cpu")
    rc = int(rc) if rc is not None else 0
    if rc < 0:
        tel["errors"] += 1
    tel["cpu"] += 1
    return rc


def stats() -> dict:
    """per-stage 耗时 / 回退计数（遥测）。QASCADE_CE_PROF=1 时 stages_ms
    含 wall_ms / gil_ms（GIL 持有采样 = 线程 CPU 时间代理）。"""
    return {
        "backend": _CACHE["backend"] or detect_backend(),
        "threads": _CACHE.get("threads") or _parse_threads(),
        "gpu_name": _gpu_dev.name if _gpu_dev is not None else None,
        "gpu_caps": _gpu_dev.caps if _gpu_dev is not None else None,
        "gpu_init_fail": _TEL["gpu_init_fail"],
        "unknown_kernels": _TEL["unknown_kernels"],
        "last_error": _TEL["last_error"],
        "kernels": dict(_TEL["kernels"]),
        "stages_ms": dict(_TEL["stages"]),
    }
