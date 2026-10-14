"""Authenticated, process-owned handoff of nested guard exit evidence.

CPython 3.13 AF_PIPE/JSON bytes and Microsoft named-pipe peer PID APIs are
reused. No mutable coordination files, pickle messages, command allowlists,
or process-name exemptions are used. The outer Job and budgets remain active.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import threading
import time
from multiprocessing.connection import Client, Listener

import psutil

from validation_guard_handles import ProofHandles

CAPABILITY_KEYS = tuple("QASCADE_GUARD_DELEGATION_" + key for key in ("PIPE", "KEY", "OWNER"))
MAX_MESSAGE = 64 * 1024
# Existing cleanup: two wait_procs(3s), root.wait(3s), settle(.5s), plus bounded IPC.
# This is evidence/cleanup time only; monitor still enforces the unchanged run timeout.
HANDOFF_SECONDS = 16


def identity(process):
    return {"pid": process.pid, "created": process.create_time()}


def same_identity(expected, actual):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    pid, created = actual.get("pid"), actual.get("created")
    valid = (type(pid) is int and pid > 0 and type(created) in (float, int)
             and math.isfinite(created) and created > 0)
    return valid and expected.get("pid") == pid and expected.get("created") == created


def match_finished(actual, reported):
    keys = ("event", "launcher_code", "child_code", "child_code_hex", "reason")
    if not isinstance(actual, dict) or not isinstance(reported, dict):
        return False
    code = actual.get("child_code")
    valid = type(code) is int and type(actual.get("launcher_code")) is int
    valid = valid and actual.get("event") == "finished"
    valid = valid and actual.get("child_code_hex") == f"0x{code & 0xffffffff:08x}"
    return valid and all(actual.get(key) == reported.get(key) for key in keys)


def _peer_pid(connection, role):
    from ctypes import wintypes
    function = getattr(ctypes.WinDLL("kernel32", use_last_error=True), "GetNamedPipe" + role + "ProcessId")
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    function.restype = wintypes.BOOL
    pid = wintypes.ULONG()
    if not function(connection.fileno(), ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    return pid.value


def _send(connection, item):
    data = json.dumps(item, allow_nan=False).encode("utf-8")
    if len(data) > MAX_MESSAGE:
        raise ValueError("delegation message exceeds bounded metadata size")
    connection.send_bytes(data)


def _receive(connection, timeout=3):
    if not connection.poll(timeout):
        raise TimeoutError("guard delegation acknowledgement expired")
    return json.loads(connection.recv_bytes(MAX_MESSAGE))


def _budget(options):
    return {key: value for key, value in vars(options).items()
            if key.endswith("gib") or key in ("timeout", "interval", "max_processes", "max_log_mib")}


def _proof_records(path):
    result = {"child_exit": {}}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            event = item.get("event")
            if event == "child_exit":
                result[event][(item.get("pid"), item.get("created"))] = item.get("code")
            elif event in ("preflight", "started", "finished", "tree_stopped"):
                result[event] = item
    return result


def _copy_file(source, target, maximum, on_write):
    before = source.stat()
    if before.st_size > maximum:
        raise ValueError("delegated evidence exceeds outer output budget")
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as reader, target.open("xb") as writer:
        while block := reader.read(64 * 1024):
            size += len(block)
            if size > maximum:
                raise ValueError("delegated evidence grew beyond outer output budget")
            writer.write(block)
            digest.update(block)
            on_write(len(block))
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("delegated evidence changed during archival")
    return {"path": str(target), "bytes": size, "sha256": digest.hexdigest()}


class Lease:
    """Specific live process identities; pending ownership is never completion."""
    def __init__(self, launcher, child, request, deadline, *, handles=None):
        self.launcher, self.root, self.request = launcher, child, request
        self.nonce, self.deadline = secrets.token_hex(32), deadline
        self.state, self.observed = "pending", {}
        self.processes, self.handles = {child: child}, handles or ProofHandles()
        self.handles.adopt(child)

    def adopt(self, process):
        if process in self.processes:
            return True
        if self.state != "pending":
            return False
        parent = next((owned for owned in self.processes if owned.pid == process.ppid()), None)
        direct = parent is not None and self.handles.parent_window(parent, process)
        live_chain = not direct and self.root.is_running() and self.root in process.parents()
        if not direct and not live_chain:
            return False
        self.handles.adopt(process)
        self.processes[process] = process
        return True

    def decision(self, process, code):
        if process not in self.processes:
            return "unknown"
        key = (process.pid, process.create_time())
        previous = self.observed.get(key)
        if self.state == "complete" and previous != code:
            return "rejected"
        self.observed[key] = code
        if self.state == "pending" and (time.monotonic() > self.deadline or not self.launcher.is_running()):
            self.state = "rejected"
        return self.state

    def verify(self, reported, processes):
        proof = _proof_records(Path(self.request["evidence"]))
        if not match_finished(proof.get("finished"), reported):
            raise ValueError("nested finished evidence differs from raw result")
        started = proof.get("started", {})
        if not same_identity(identity(self.root), started):
            raise ValueError("nested evidence belongs to another root process")
        preflight = proof.get("preflight", {})
        if preflight.get("launcher_pid") != self.launcher.pid or preflight.get("log") != self.request["log"]:
            raise ValueError("nested preflight does not belong to the authenticated launcher")
        raw = self.handles.poll(self.root)
        if raw is None or raw & 0xffffffff != reported["child_code"] & 0xffffffff:
            raise ValueError("nested root kernel exit differs from raw evidence")
        if proof.get("tree_stopped", {}).get("remaining_pids") != []:
            raise ValueError("nested guard did not prove owned-tree cleanup")
        codes = self._settled_codes(processes)
        return self._verify_descendants(proof, reported, codes)

    def _settled_codes(self, processes):
        deadline = min(self.deadline, time.monotonic() + 0.5)
        while True:
            codes = {process: self.handles.poll(process) for process in processes}
            missing = [process for process, code in codes.items() if code is None]
            if not missing:
                return codes
            if time.monotonic() >= deadline:
                raise ValueError("nested retained kernel process still active: " + str([identity(p) for p in missing]))
            time.sleep(0.01)

    def _verify_descendants(self, proof, reported, codes):
        observed = {}
        for process, code in codes.items():
            if code & 0x80000000:
                key = (process.pid, process.create_time())
                observed[key] = code
                if reported["launcher_code"] == 0:
                    raise ValueError("native failure cannot be a successful inner run")
                recorded = reported["child_code"] if process == self.root else proof["child_exit"].get(key)
                if type(recorded) is not int or recorded & 0xffffffff != code & 0xffffffff:
                    raise ValueError("nested evidence omitted a specific native descendant exit")
        return observed


class DelegationServer:
    def __init__(self, options):
        self.owner, self.root = identity(psutil.Process()), None
        self.key, self.nonce = secrets.token_bytes(32), secrets.token_hex(32)
        self.listener = Listener(family="AF_PIPE", authkey=self.key)
        self.address, self.options = self.listener.address, options
        self.deadline = time.monotonic() + options.timeout
        self.lock, self.stopped, self.events = threading.RLock(), threading.Event(), Queue()
        self.leases, self.workers, self.archived_bytes = [], [], 0
        self.completed, self.rejected = {}, False
        self.archive = Path(str(options.log) + ".delegated")
        self.acceptor = threading.Thread(target=self._accept, daemon=True)
        self.acceptor.start()

    def environment(self):
        owner = {**self.owner, "nonce": self.nonce}
        return dict(zip(CAPABILITY_KEYS, (self.address, self.key.hex(), json.dumps(owner))))

    def set_root(self, process):
        self.root = process

    def _accept(self):
        while not self.stopped.is_set():
            try:
                connection = self.listener.accept()
                if self.stopped.is_set():
                    connection.close()
                    break
                worker = threading.Thread(target=self._serve, args=(connection,), daemon=True)
                self.workers.append(worker)
                worker.start()
            except (OSError, EOFError):
                if not self.stopped.is_set():
                    self.events.put(("delegation_error", {"reason": "pipe_accept_failed"}))

    def _begin(self, connection, request):
        launcher = psutil.Process(_peer_pid(connection, "Client"))
        self._check_scope(launcher, request)
        timeout = self._checked_timeout(request["budgets"]["timeout"])
        handles = ProofHandles()
        try:
            child = handles.receive(launcher, request["child"])
            self._check_peer(launcher, child, request)
            deadline = min(self.deadline, time.monotonic() + timeout + HANDOFF_SECONDS)
            lease = Lease(launcher, child, request, deadline, handles=handles)
        except BaseException:
            handles.close()
            raise
        with self.lock:
            self.leases.append(lease)
        self.events.put(("delegation_registered", {"child": identity(child), "launcher": identity(launcher),
                                                 "log": request["log"], "budgets": request["budgets"]}))
        return lease

    @staticmethod
    def _check_peer(launcher, child, request):
        if not same_identity(identity(launcher), request["launcher"]):
            raise ValueError("delegation launcher identity does not match pipe peer")
        if not same_identity(identity(child), request["child"]) or child.ppid() != launcher.pid:
            raise ValueError("delegation child is not the live peer-owned process")

    def _check_scope(self, launcher, request):
        if self.root is None or (launcher != self.root and self.root not in launcher.parents()):
            raise ValueError("delegation launcher is outside the guarded root")
        if request["outer"] != {**self.owner, "nonce": self.nonce}:
            raise ValueError("delegation belongs to another outer guard lifetime")

    @staticmethod
    def _checked_timeout(timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("nested timeout is not bounded")
        return timeout

    def _serve(self, connection):
        lease = None
        try:
            lease = self._begin(connection, _receive(connection))
            _send(connection, {"nonce": lease.nonce})
            while not self.stopped.is_set():
                if not connection.poll(0.1):
                    continue
                item = _receive(connection)
                if self._dispatch(lease, item, connection):
                    return
        except (Exception,) as error:
            with self.lock:
                self.rejected = True
                if lease is not None:
                    lease.state = "rejected"
            self.events.put(("delegation_rejected", {"error": str(error)}))
        finally:
            connection.close()

    def _dispatch(self, lease, item, connection):
        if item.get("nonce") != lease.nonce:
            raise ValueError("delegation nonce or connection lifetime differs")
        if item.get("event") == "finished":
            self._finish(lease, item["record"])
            _send(connection, {"accepted": True})
            return True
        if item.get("event") == "final_exits":
            _send(connection, {"processes": self._final_exits(lease)})
        else:
            self._adopt(lease, item)
            _send(connection, {"accepted": True})
        return False

    def _final_exits(self, lease):
        with self.lock:
            if lease.state != "pending" or time.monotonic() > lease.deadline or not lease.launcher.is_running():
                raise ValueError("nested final kernel receipt ownership expired")
            return [lease.handles.receipt(process) for process in lease.processes]

    def _adopt(self, lease, item):
        expected = item.get("child")
        if item.get("event") != "descendant" or not same_identity(expected, expected):
            raise ValueError("delegation descendant identity differs")
        with self.lock:
            existing = next((owned for owned in lease.processes if same_identity(identity(owned), expected)), None)
            if existing is not None:
                lease.handles.lifetime(existing)  # Already-retained exact kernel object, including retired PIDs.
                return
        child = lease.handles.receive(lease.launcher, expected)
        with self.lock:
            if lease.adopt(child):
                return
        lease.handles.release(child)
        raise ValueError("delegation descendant has no captured owned ancestry")

    def _finish(self, lease, reported):
        with self.lock:
            if lease.state != "pending" or time.monotonic() > lease.deadline or not lease.launcher.is_running():
                raise ValueError("nested ownership expired before completion")
            processes = tuple(lease.processes)
        # Log reads and archival never hold the registry lock used by the monitor.
        maximum = self._remaining_output()
        if Path(lease.request["evidence"]).stat().st_size > maximum:
            raise ValueError("nested proof exceeds outer output budget")
        observed = lease.verify(reported, processes)
        copies = self._archive(lease)
        with self.lock:
            if (tuple(lease.processes) != processes or lease.state != "pending"
                    or time.monotonic() > lease.deadline or not lease.launcher.is_running()):
                raise ValueError("nested owned identities changed during proof archival")
            lease.observed.update(observed)
            lease.state = "complete"
            self.completed = getattr(self, "completed", {})
            self.completed.update(lease.observed)
            self.leases.remove(lease)
            self.events.put(("delegation_completed", {"child": identity(lease.root), "finished": reported,
                "native_exits": [{"pid": pid, "created": created, "code": code}
                                 for (pid, created), code in lease.observed.items()], "evidence": copies}))
        lease.handles.close()

    def _remaining_output(self):
        return int(self.options.max_log_mib * 1024 ** 2) - self.archived_bytes - self.options.log.stat().st_size

    def _archive(self, lease):
        self.archive.mkdir(exist_ok=True)
        copies = []
        for key in ("log", "evidence"):
            target = self.archive / (lease.nonce + "." + key)
            copies.append(_copy_file(Path(lease.request[key]), target, self._remaining_output(), self._archive_written))
        return copies

    def _archive_written(self, count):
        with self.lock:
            self.archived_bytes += count

    def observe(self, process):
        with self.lock:
            for lease in self.leases:
                if lease.adopt(process):
                    return

    def decision(self, process, code):
        with self.lock:
            previous = getattr(self, "completed", {}).get((process.pid, process.create_time()))
            if previous is not None:
                return "complete" if previous == code else "rejected"
            for lease in self.leases:
                state = lease.decision(process, code)
                if state != "unknown":
                    return state
        return "unknown"

    def unresolved(self):
        with self.lock:
            return getattr(self, "rejected", False) or bool(self.leases)

    def drain(self, evidence):
        while True:
            try:
                event, fields = self.events.get_nowait()
            except Empty:
                return
            evidence.emit(event, **fields)

    def close(self):
        self.stopped.set()
        wake = Client(self.address, family="AF_PIPE", authkey=self.key)
        wake.close()
        self.acceptor.join(timeout=2)
        self.listener.close()
        for worker in self.workers:
            worker.join(timeout=2)
        for lease in self.leases:
            lease.handles.close()


class DelegationClient:
    def __init__(self, environment):
        self.address = environment[CAPABILITY_KEYS[0]]
        self.key = bytes.fromhex(environment[CAPABILITY_KEYS[1]])
        self.outer = json.loads(environment[CAPABILITY_KEYS[2]])
        self.connection, self.nonce = None, None
        self.handles = ProofHandles()

    def register(self, process, options):
        self.connection = Client(self.address, family="AF_PIPE", authkey=self.key)
        server = psutil.Process(_peer_pid(self.connection, "Server"))
        if not same_identity(self.outer, identity(server)):
            raise ValueError("delegation server identity differs from inherited capability")
        request = {"launcher": identity(psutil.Process()), "child": self.handles.receipt(process), "outer": self.outer,
                   "log": str(options.log), "evidence": str(options.log) + ".guard.jsonl", "budgets": _budget(options)}
        _send(self.connection, request)
        self.nonce = _receive(self.connection)["nonce"]

    def descendant(self, process):
        _send(self.connection, {"event": "descendant", "nonce": self.nonce,
                                "child": self.handles.receipt(process)})
        _receive(self.connection)

    def final_exits(self, root, evidence):
        _send(self.connection, {"event": "final_exits", "nonce": self.nonce})
        receipts = _receive(self.connection)["processes"]
        server = psutil.Process(_peer_pid(self.connection, "Server"))
        if not same_identity(self.outer, identity(server)):
            raise ValueError("final receipt server identity differs")
        processes = [self.handles.receive(server, receipt) for receipt in receipts]
        native, deadline = False, time.monotonic() + 0.5
        for process in processes:
            if not self.handles.owned_descendant(root, process):
                raise ValueError("final kernel receipt has no captured owned parent chain")
            if process != root:
                code = self._final_code(process, deadline)
                evidence.emit("child_exit", pid=process.pid, created=process.create_time(), code=code,
                              phase="after_owned_cleanup")
                native = native or bool(code & 0x80000000)
        return native

    def _final_code(self, process, deadline):
        while True:
            code = self.handles.poll(process)
            if code is not None:
                return code
            if time.monotonic() >= deadline:
                raise ValueError("final kernel receipt still active after cleanup")
            time.sleep(0.01)

    def finish(self, record):
        _send(self.connection, {"event": "finished", "nonce": self.nonce, "record": record})
        if _receive(self.connection).get("accepted") is not True:
            raise ValueError("outer guard rejected nested raw evidence")

    def close(self):
        if self.connection is not None:
            self.connection.close()
        self.handles.close()


def client_from_environment():
    present = [key in os.environ for key in CAPABILITY_KEYS]
    if any(present) and not all(present):
        raise ValueError("incomplete outer guard capability")
    return DelegationClient(os.environ) if all(present) and os.name == "nt" else None
