"""Qascade CE 后端探测 — 自动识别可用后端（零配置）。

P0: CPU 后端（AVX2/SSE2 变体自动选择）。
P5: GPU 后端接入点 — 探测链: NVIDIA→CUDA(ce_gpu_cuda.dll) /
    AMD→OpenCL(ce_gpu_opencl.dll) / 无→CPU 降级。
    统一 API 见 csrc/ce_gpu_api.h（init/build/run/destroy + 能力位）;
    GPU 计算替换点接入需工程机 NVIDIA 实测位级对拍后合入（P5 待办:
    render_lab/bench/ce_probe_gpu_equiv.py 是合入门）。
    DLL 候选目录: ①包目录(源码/onedir) ②QASCADE_CE_GPU_DIR(外部部署)
    ③frozen: exe 同目录 / 开发态: cwd（onefile _MEIPASS 只读不可注入）。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys

from .ctypes_api import CEEngine, load_engine

log = logging.getLogger(__name__)

# 后端优先级: gpu(cuda/opencl) > cpu(avx2) > cpu(sse2)
BACKENDS = ("cpu-avx2", "cpu-sse2")

_GPU_DLLS = (("ce_gpu_cuda.dll", 1, "gpu-cuda"),
             ("ce_gpu_opencl.dll", 2, "gpu-opencl"))


def _gpu_dll_dirs() -> tuple[str, ...]:
    """GPU DLL 候选目录（按序探测, 去重后返回）。

    ① 包目录（__file__ 所在, 源码/onedir 部署）;
    ② QASCADE_CE_GPU_DIR（外部 DLL 目录——onefile exe 部署, _MEIPASS
       只读不可写, 4080 方案 A: 正式包 + 外部目录放 ce_gpu_cuda.dll）;
    ③ frozen: exe 同目录（sys.executable 的 dirname）; 开发态: cwd。
    CUDA/OpenCL 优先级由 _GPU_DLLS 顺序决定（外部目录的 CUDA 同样先于
    包内 OpenCL, 只要 cuda 名在先即优先）。
    """
    dirs = [os.path.dirname(os.path.abspath(__file__))]
    env_dir = os.environ.get("QASCADE_CE_GPU_DIR")
    if env_dir:
        dirs.append(env_dir)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        dirs.append(os.getcwd())
    seen = set()
    out = []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return tuple(out)


def load_gpu_backend() -> str | None:
    """GPU 探测链: NVIDIA→CUDA DLL / AMD→OpenCL DLL / 无→None。

    返回描述串（"gpu-cuda:RTX 4070" 之类）供日志/后端名; None = 无 GPU。
    仅探测+初始化, 不接渲染替换点（P5: 工程机 NVIDIA 位级对拍后接入）。
    DLL 位置: _gpu_dll_dirs() 逐目录尝试（onefile 场景从外部目录加载）。
    """
    for dll_name, backend_id, tag in _GPU_DLLS:
        for dll_dir in _gpu_dll_dirs():
            path = os.path.join(dll_dir, dll_name)
            if not os.path.exists(path):
                continue
            try:
                dll = ctypes.CDLL(path)
                dll.ce_gpu_probe.restype = ctypes.c_int
                if dll.ce_gpu_probe() == 0:
                    continue
                dll.ce_gpu_init.restype = ctypes.c_void_p
                dll.ce_gpu_init.argtypes = [ctypes.c_int, ctypes.c_char_p]
                dll.ce_gpu_name.restype = ctypes.c_char_p
                dll.ce_gpu_name.argtypes = [ctypes.c_void_p]
                dll.ce_gpu_destroy.argtypes = [ctypes.c_void_p]
                dev = dll.ce_gpu_init(backend_id, None)
                name = dll.ce_gpu_name(dev).decode("utf-8", "replace") if dev else ""
                if dev:
                    dll.ce_gpu_destroy(dev)
                return f"{tag}:{name}" if name else tag
            except Exception as e:
                log.warning("[CE] GPU 探测异常 %s@%s: %s", dll_name, dll_dir, e)
    return None


class Backend:
    """已解析的渲染后端。"""

    def __init__(self, name: str, engine: CEEngine | None, threads: int):
        self.name = name            # "cpu-avx2" / "cpu-sse2" / "gpu-cuda" / "gpu-opencl"
        self.engine = engine        # CEEngine 或 None（纯回退）
        self.threads = threads

    def __repr__(self):
        return f"<Backend {self.name} threads={self.threads}>"


def detect_backend() -> Backend:
    """零配置探测：CPU 变体选择；QASCADE_CE_GPU=1 时额外报告 GPU 后端。

    GPU 后端探测结果仅入日志（计算替换点接入待 P5 合入, 合入门
    = 工程机 NVIDIA 跑通 bench/ce_probe_gpu_equiv.py 全 PASS）。
    """
    engine = load_engine()
    if engine is None:
        return Backend("python-fallback", None, 1)
    threads = max(1, engine.cpu_cores())
    if os.environ.get("QASCADE_CE_GPU", "0") == "1":
        gpu = load_gpu_backend()
        if gpu is not None:
            log.info("[CE] GPU 后端可用: %s（渲染替换点待 P5 接入, 计算仍走 CPU）",
                     gpu)
    name = f"cpu-{engine.variant}"
    log.info("[CE] backend=%s threads=%d", name, threads)
    return Backend(name, engine, threads)
