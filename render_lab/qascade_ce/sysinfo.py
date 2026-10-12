# -*- coding: utf-8 -*-
"""Qascade CE — 主机资源信息（物理核检测 / 启动资源行）。

用途:
  * 阶段3.1: OMP_NUM_THREADS 默认值 = 物理核数（本机 8 核开发机 → 默认不变;
    16 物理核的 7945HX → 16, 取代旧的硬编码 8）。
  * 阶段0.2: 启动日志一行汇总（核数 / OMP / GPU DLL 门控），目标机零操作可核查。

退化链（任何一步失败继续下一个, 最终必有值）:
  1. psutil.cpu_count(logical=False)        —— 最准, 但可能未随包
  2. ctypes GetLogicalProcessorInformationEx —— 纯 Win32, 无第三方依赖
  3. os.cpu_count() // 2                    —— 假设 SMT=2 的粗估
"""
from __future__ import annotations

import ctypes
import os
import sys

_LOGICAL = os.cpu_count() or 1

# ── Win32 GetLogicalProcessorInformationEx ────────────────────────────
# RelationProcessorCore = 0; SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX:
#   DWORD Relationship; DWORD Size; 然后 union（Processor 组）
#   PROCESSOR_RELATIONSHIP { BYTE Flags; BYTE EfficiencyClass; BYTE Reserved[20];
#                            WORD GroupCount; GROUP_AFFINITY GroupMask[] }
#   GROUP_AFFINITY { KAFFINITY(ULONG_PTR) Mask; WORD Group; WORD Reserved[3] }
_RELATION_PROCESSOR_CORE = 0


def _physical_cores_win32() -> int:
    """GetLogicalProcessorInformationEx(RelationProcessorCore) 计数。"""
    if sys.platform != "win32":
        return 0
    try:
        k32 = ctypes.windll.kernel32
        fn = k32.GetLogicalProcessorInformationEx
        fn.argtypes = [ctypes.c_int, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_uint32)]
        fn.restype = ctypes.c_int
        length = ctypes.c_uint32(0)
        fn(_RELATION_PROCESSOR_CORE, None, ctypes.byref(length))  # 取长度
        if length.value == 0:
            return 0
        buf = ctypes.create_string_buffer(length.value)
        if not fn(_RELATION_PROCESSOR_CORE, buf, ctypes.byref(length)):
            return 0
        n = 0
        off = 0
        while off + 8 <= length.value:
            rel = int.from_bytes(buf[off:off + 4], "little")
            size = int.from_bytes(buf[off + 4:off + 8], "little")
            if size < 8:
                break
            if rel == _RELATION_PROCESSOR_CORE:
                n += 1
            off += size
        return n
    except Exception:
        return 0


_CORES_CACHE: int | None = None


def physical_cores() -> int:
    """物理核数（psutil → Win32 → 逻辑//2 退化链, 进程内缓存）。"""
    global _CORES_CACHE
    if _CORES_CACHE is not None:
        return _CORES_CACHE
    n = 0
    try:
        import psutil
        n = int(psutil.cpu_count(logical=False) or 0)
    except Exception:
        n = 0
    if n <= 0:
        n = _physical_cores_win32()
    if n <= 0:
        n = max(1, _LOGICAL // 2)
    _CORES_CACHE = n
    return n


def cpu_thread_default() -> int:
    """OMP/工作线程默认值 = 物理核数（下限 1, 上限逻辑核数）。"""
    n = physical_cores()
    if n < 1:
        n = 1
    if n > _LOGICAL:
        n = _LOGICAL
    return n


def gpu_dll_status() -> str:
    """GPU DLL 存在性（零加载成本, 不做 probe/init —— 保持懒探测纪律）。"""
    dirs = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        dirs.append(here)
        env_dir = os.environ.get("QASCADE_CE_GPU_DIR")
        if env_dir:
            dirs.append(env_dir)
        if getattr(sys, "frozen", False):
            dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        pass
    found = []
    for name in ("ce_gpu_cuda.dll", "ce_gpu_opencl.dll"):
        for d in dirs:
            try:
                if os.path.exists(os.path.join(d, name)):
                    found.append(name)
                    break
            except Exception:
                pass
    gate = os.environ.get("QASCADE_CE_GPU", "0")
    if not found:
        return "无（纯 CPU; 门控 %s）" % gate
    return "%s（门控 %s）" % ("+".join(found), gate)


def resource_line() -> str:
    """启动资源行（单行, 供日志）。"""
    return ("资源: CPU %d 物理核 / %d 逻辑核 | RAM %.1fGB 总 %.1fGB 可用 | "
            "OMP_NUM_THREADS=%s OMP_WAIT_POLICY=%s | GPU DLL %s"
            % (physical_cores(), _LOGICAL,
               _ram_gb()[0], _ram_gb()[1],
               os.environ.get("OMP_NUM_THREADS", "(未设)"),
               os.environ.get("OMP_WAIT_POLICY", "(未设)"),
               gpu_dll_status()))


def _ram_gb() -> tuple[float, float]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return (round(vm.total / 1e9, 1), round(vm.available / 1e9, 1))
    except Exception:
        pass
    try:
        class _MEMSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32),
                        ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64),
                        ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64),
                        ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64),
                        ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]
        m = _MEMSTATUSEX()
        m.dwLength = ctypes.sizeof(_MEMSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return (round(m.ullTotalPhys / 1e9, 1),
                    round(m.ullAvailPhys / 1e9, 1))
    except Exception:
        pass
    return (0.0, 0.0)
