"""Windows 验收进程的内核预算；只接线官方 API，不更改系统设置。

布局/API 来源见 .research_cache/r185_validation_budget.md 与 Microsoft Learn。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as w


class PerformanceInfo(ctypes.Structure):
    _fields_ = [("cb", w.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal",
            "PhysicalAvailable", "SystemCache", "KernelTotal", "KernelPaged",
            "KernelNonpaged", "PageSize",
        )
    ] + [(name, w.DWORD) for name in ("HandleCount", "ProcessCount", "ThreadCount")]


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
        ("SchedulingClass", w.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters)] + [
        (name, ctypes.c_size_t) for name in (
            "ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed",
            "PeakJobMemoryUsed",
        )
    ]


def _api(dll, name, result, args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, args
    return function


def _kernel():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _check(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def set_local_error_mode():
    """保留已有错误模式，后代继承本进程模式；原生异常仍有非零退出码。"""
    kernel = _kernel()
    get_mode = _api(kernel, "GetErrorMode", w.UINT, [])
    set_mode = _api(kernel, "SetErrorMode", w.UINT, [w.UINT])
    previous = get_mode()
    set_mode(previous | 0x0001 | 0x0002)
    return previous


def commit_snapshot():
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    get_info = _api(psapi, "GetPerformanceInfo", w.BOOL,
                    [ctypes.POINTER(PerformanceInfo), w.DWORD])
    info = PerformanceInfo()
    info.cb = ctypes.sizeof(info)
    _check(get_info(ctypes.byref(info), info.cb))
    return {
        "commit_total": info.CommitTotal * info.PageSize,
        "commit_limit": info.CommitLimit * info.PageSize,
        "commit_free": (info.CommitLimit - info.CommitTotal) * info.PageSize,
    }


class WindowsJob:
    """匿名 Job 只收纳本次挂起创建的 child；失败时不执行无保护作业。"""

    def __init__(self, max_bytes):
        kernel = _kernel()
        self._close = _api(kernel, "CloseHandle", w.BOOL, [w.HANDLE])
        self._assign = _api(kernel, "AssignProcessToJobObject", w.BOOL,
                            [w.HANDLE, w.HANDLE])
        self._terminate = _api(kernel, "TerminateJobObject", w.BOOL,
                               [w.HANDLE, w.UINT])
        self._query = _api(kernel, "QueryInformationJobObject", w.BOOL,
                           [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID])
        create = _api(kernel, "CreateJobObjectW", w.HANDLE, [w.LPVOID, w.LPCWSTR])
        configure = _api(kernel, "SetInformationJobObject", w.BOOL,
                         [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD])
        self.handle = _check(create(None, None))
        limits = ExtendedLimits()
        # JOB_MEMORY bounds commit before allocation; closing the sole handle cleans orphans.
        limits.BasicLimitInformation.LimitFlags = 0x00000200 | 0x00002000
        limits.JobMemoryLimit = max_bytes
        try:
            _check(configure(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def attach_and_resume(self, process):
        """子进程 CREATE_SUSPENDED，先加入 Job，恢复时线程句柄必须属于该进程。"""
        kernel = _kernel()
        open_thread = _api(kernel, "OpenThread", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD])
        owner = _api(kernel, "GetProcessIdOfThread", w.DWORD, [w.HANDLE])
        resume = _api(kernel, "ResumeThread", w.DWORD, [w.HANDLE])
        threads = process.threads()
        if len(threads) != 1 or not process.is_running():
            raise RuntimeError("suspended child must have exactly one live primary thread")
        # THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION.
        thread = _check(open_thread(0x0002 | 0x0800, False, threads[0].id))
        try:
            if owner(thread) != process.pid or not process.is_running():
                raise RuntimeError("primary thread no longer belongs to launched child")
            _check(self._assign(self.handle, int(process._handle)))
            previous = resume(thread)
            if previous != 1:
                raise RuntimeError(f"unexpected primary-thread suspend count: {previous}")
        finally:
            _check(self._close(thread))

    def memory_snapshot(self):
        limits = ExtendedLimits()
        _check(self._query(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), None))
        return {"job_peak_memory_reported": limits.PeakJobMemoryUsed,
                "job_memory_limit": limits.JobMemoryLimit}

    def stop(self, exit_code):
        _check(self._terminate(self.handle, exit_code))

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            _check(self._close(handle))


def windows_job(max_bytes):
    return WindowsJob(max_bytes) if os.name == "nt" else None


class ChildExitHandles:
    """持有只读子进程句柄，保留原生退出码并避免 PID 回收后的错误观察。"""

    def __init__(self):
        kernel = _kernel()
        self._open = _api(kernel, "OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD])
        self._exit = _api(kernel, "GetExitCodeProcess", w.BOOL, [w.HANDLE, ctypes.POINTER(w.DWORD)])
        self._times = _api(kernel, "GetProcessTimes", w.BOOL, [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4)
        self._close = _api(kernel, "CloseHandle", w.BOOL, [w.HANDLE])
        self.handles = {}

    def adopt(self, process):
        if process in self.handles or not process.is_running():
            return
        handle = self._open(0x00100000 | 0x1000, False, process.pid)
        if not handle and not process.is_running():
            return
        _check(handle)
        created, exited, kernel, user = (w.FILETIME() for _ in range(4))
        try:
            _check(self._times(handle, ctypes.byref(created), ctypes.byref(exited),
                               ctypes.byref(kernel), ctypes.byref(user)))
            timestamp = ((created.dwHighDateTime << 32 | created.dwLowDateTime) / 10 ** 7) - 11644473600
            if abs(timestamp - process.create_time()) > 0.00001:
                return
            self.handles[process] = handle
            handle = None
        finally:
            if handle:
                _check(self._close(handle))

    def poll(self, process):
        handle = self.handles.get(process)
        if handle is None:
            return None
        code = w.DWORD()
        _check(self._exit(handle, ctypes.byref(code)))
        return None if code.value == 259 else code.value

    def close(self):
        for handle in self.handles.values():
            _check(self._close(handle))
        self.handles.clear()
