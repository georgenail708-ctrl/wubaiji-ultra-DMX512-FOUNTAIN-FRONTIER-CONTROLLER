"""Read-only kernel receipts for authenticated nested-guard ownership.

Microsoft DuplicateHandle and PROCESS_BASIC_INFORMATION are used directly.
The sender retains its source handle; only the authenticated pipe peer is
opened for duplication. PID, creation time and actual parent come from the
duplicated kernel object, including after its process has exited.
"""
from __future__ import annotations

import ctypes
import math
from ctypes import wintypes as w

from _validation_windows import ChildExitHandles, _api, _check, _kernel

READ_PROCESS = 0x00100000 | 0x0400 | 0x1000


class BasicProcessInformation(ctypes.Structure):
    _fields_ = [("reserved1", w.LPVOID), ("peb", w.LPVOID),
                ("reserved2", w.LPVOID * 2), ("pid", ctypes.c_size_t),
                ("parent_pid", ctypes.c_size_t)]


class KernelProcess:
    """Cached, verified identity; never reopen a retired process by PID."""
    def __init__(self, pid, created, parent_pid, handles):
        self.pid, self.created, self.parent_pid, self.owner = pid, created, parent_pid, handles

    def create_time(self):
        return self.created

    def ppid(self):
        return self.parent_pid

    def is_running(self):
        return self.owner.poll(self) is None

    def parents(self):
        return []  # Ownership is established from captured direct kernel parents.

    def __hash__(self):
        return hash((self.pid, self.created))

    def __eq__(self, other):
        return hasattr(other, "create_time") and (self.pid, self.created) == (other.pid, other.create_time())


class ProofHandles(ChildExitHandles):
    """Hold a query/synchronize handle or fail before claiming ownership."""
    def adopt(self, process):
        if process in self.handles:
            return
        handle = _check(self._open(READ_PROCESS, False, process.pid))
        self._retain(process, handle)

    def _retain(self, process, handle):
        self.handles[process] = handle
        try:
            created, _exited = self.lifetime(process)
            if abs(created - process.create_time()) > 0.00001:
                raise ValueError("retained kernel handle belongs to a reused PID")
        except BaseException:
            self.release(process)
            raise

    def lifetime(self, process):
        return self.handle_lifetime(self.handles[process])

    def handle_lifetime(self, handle):
        times = [w.FILETIME() for _ in range(4)]
        _check(self._times(handle, *(ctypes.byref(value) for value in times)))
        values = [(value.dwHighDateTime << 32 | value.dwLowDateTime) for value in times[:2]]
        return tuple(value / 10 ** 7 - 11644473600 if value else 0 for value in values)

    def parent_window(self, parent, child):
        created, exited = self.lifetime(parent)
        born = child.create_time()
        return created <= born and (not exited or born <= exited)

    def release(self, process):
        handle = self.handles.pop(process, None)
        if handle is not None:
            _check(self._close(handle))

    def receipt(self, process):
        self.adopt(process)
        return {"pid": process.pid, "created": process.create_time(), "handle": int(self.handles[process])}

    def parent_pid(self, process):
        return _process_identity(self.handles[process])[1]

    def owned_descendant(self, root, process):
        seen = set()
        while process != root:
            if process in seen:
                return False
            seen.add(process)
            parent = self.parent_pid(process)
            candidates = [owned for owned in self.handles if owned.pid == parent
                          and self.parent_window(owned, process)]
            if len(candidates) != 1:
                return False
            process = candidates[0]
        return True

    def receive(self, peer, expected):
        source, created = _checked_receipt(expected)
        handle = self._duplicate(peer, source)
        try:
            pid, parent = _process_identity(handle)
            actual_created, _exited = self.handle_lifetime(handle)
            if pid != expected.get("pid") or abs(actual_created - created) > 0.00001:
                raise ValueError("duplicated kernel handle belongs to another process lifetime")
            process = KernelProcess(pid, expected["created"], parent, self)
            if process in self.handles:
                self.lifetime(process)
                return next(owned for owned in self.handles if owned == process)
            retained, handle = handle, None
            self._retain(process, retained)
            return process
        finally:
            if handle is not None and handle not in self.handles.values():
                _check(self._close(handle))

    def _duplicate(self, peer, source):
        kernel = _kernel()
        duplicate = _api(kernel, "DuplicateHandle", w.BOOL,
                         [w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD])
        current = _api(kernel, "GetCurrentProcess", w.HANDLE, [])
        temporary = ProofHandles()
        peer_handle = _check(self._open(0x0040 | READ_PROCESS, False, peer.pid))
        try:
            # Check the peer kernel object before using its handle table.
            token = KernelProcess(peer.pid, peer.create_time(), 0, self)
            retained, peer_handle = peer_handle, None
            temporary._retain(token, retained)
            target = w.HANDLE()
            _check(duplicate(temporary.handles[token], source, current(), ctypes.byref(target),
                             READ_PROCESS, False, 0))
            return target.value
        finally:
            if peer_handle is not None:
                _check(self._close(peer_handle))
            temporary.close()


def _checked_receipt(expected):
    source, created, pid = (expected.get(key) for key in ("handle", "created", "pid"))
    if type(source) is not int or source <= 0:
        raise ValueError("delegated identity has no retained source kernel handle")
    if type(pid) is not int or pid <= 0:
        raise ValueError("delegated kernel receipt PID is malformed")
    if type(created) not in (float, int) or not math.isfinite(created) or created <= 0:
        raise ValueError("delegated kernel receipt creation time is malformed")
    return source, created


def _process_identity(handle):
    kernel = _kernel()
    get_pid = _api(kernel, "GetProcessId", w.DWORD, [w.HANDLE])
    pid = _check(get_pid(handle))
    # Dynamically resolved internal API: an unsupported layout/status fails closed.
    query = _api(ctypes.WinDLL("ntdll"), "NtQueryInformationProcess", ctypes.c_long,
                 [w.HANDLE, ctypes.c_int, w.LPVOID, w.ULONG, ctypes.POINTER(w.ULONG)])
    basic, returned = BasicProcessInformation(), w.ULONG()
    status = query(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic), ctypes.byref(returned))
    if status < 0 or returned.value != ctypes.sizeof(basic) or basic.pid != pid or not basic.parent_pid:
        raise ValueError("kernel process parent identity could not be verified")
    return pid, basic.parent_pid
