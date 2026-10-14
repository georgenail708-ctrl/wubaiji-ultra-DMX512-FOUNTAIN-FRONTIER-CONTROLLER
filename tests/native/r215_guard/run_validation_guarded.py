"""串行验收 launcher：独占锁、预算、仅本次进程树的清理及原始退出证据。

例：venv/Scripts/python tools/run_validation_guarded.py --log tmp_pytest.log \
    --max-gib 6 --min-free-gib 4 --timeout 1800 -- venv/Scripts/python -m pytest -q -n 8
直接复用 psutil（BSD-3-Clause）和 filelock（MIT），上游证据见
.research_cache/r185_validation_budget.md。Windows Job 保护在执行子进程前生效。
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone
from importlib.metadata import version

import psutil
from filelock import FileLock, Timeout

from _validation_windows import ChildExitHandles, commit_snapshot, set_local_error_mode, windows_job
from validation_guard_delegation import DelegationServer, client_from_environment

GIB = 1024 ** 3
ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / ".research_cache" / "validation-heavy.lock"
GUARD_STOP = 125
LOCK_BUSY = 75
BLAS_THREAD_LIMIT_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
FATAL_OUTPUT = re.compile(
    rb"(?:^|\n)(?:E\s+)?(?:MemoryError\b|Fatal Python error:|Windows fatal exception:)"
    rb"|\[WinError 1455\]|Not properly terminated"
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--max-gib", type=float, default=6)
    parser.add_argument("--min-free-gib", type=float, default=4)
    parser.add_argument("--min-commit-free-gib", type=float, default=4)
    parser.add_argument("--min-disk-free-gib", type=float, default=4)
    parser.add_argument("--max-processes", type=int, default=32)
    parser.add_argument("--max-log-mib", type=float, default=512)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    options.command = options.command[1:] if options.command[:1] == ["--"] else options.command
    if not options.command:
        parser.error("provide COMMAND after --")
    positive = (options.max_gib, options.max_processes, options.max_log_mib, options.timeout, options.interval)
    minimums = (options.min_free_gib, options.min_commit_free_gib, options.min_disk_free_gib)
    if not all(math.isfinite(value) for value in positive + minimums) or min(positive) <= 0 or min(minimums) < 0:
        parser.error("budgets, interval and timeout must be positive; minimums must be nonnegative")
    options.log = options.log.resolve()
    return options


class Evidence:
    def __init__(self, path):
        self.path = Path(str(path) + ".guard.jsonl")
        self.stream = self.path.open("x", encoding="utf-8", buffering=1)

    def emit(self, event, **fields):
        item = {"event": event, "utc": datetime.now(timezone.utc).isoformat(), **fields}
        self.stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        if event != "sample":
            print(f"[guard] {event}: {json.dumps(fields, ensure_ascii=False)}", flush=True)

    def close(self):
        self.stream.close()


def system_snapshot(log_path):
    memory = psutil.virtual_memory()
    return {
        "available": memory.available, "physical_total": memory.total,
        "disk_free": psutil.disk_usage(str(log_path.parent)).free,
        **(commit_snapshot() if os.name == "nt" else {"commit_free": None}),
    }


def resource_reason(snapshot, options, *, preflight=False):
    reserve = options.max_gib if preflight else 0
    checks = (
        ("available", (options.min_free_gib + reserve) * GIB, "physical_memory_reserve"),
        ("commit_free", (options.min_commit_free_gib + reserve) * GIB, "commit_reserve"),
        ("disk_free", options.min_disk_free_gib * GIB, "disk_reserve"),
    )
    for key, minimum, reason in checks:
        value = snapshot.get(key)
        if value is not None and value < minimum:
            return reason
    if max(snapshot.get("rss", 0), snapshot.get("private_commit", 0)) > options.max_gib * GIB:
        return "process_memory_budget"
    if snapshot.get("process_count", 0) > options.max_processes:
        return "process_count_budget"
    if snapshot.get("log_bytes", 0) > options.max_log_mib * 1024 ** 2:
        return "output_budget"
    return None


def diagnostic_identity(process):
    """Owned tree only; unavailable metadata must not change memory accounting."""
    try:
        return {"ppid": process.ppid(), "exe": process.exe()}
    except psutil.Error:
        return {"ppid": None, "exe": None}


class OwnedTree:
    """只从已启动的对象拓展后代，psutil 的身份检查防止信号误伤重用 PID。"""
    def __init__(self, process, delegation=None, reporter=None):
        self.root = process
        self.known = {process: process}
        self.recorded_exits = set()
        self.reported_exits = set()
        self.delegation, self.reporter = delegation, reporter
        self.exit_handles = (reporter.handles if reporter else ChildExitHandles()) if os.name == "nt" else None

    def discover(self):
        scanned = set()
        for process in list(self.known):
            if process in scanned or not process.is_running():
                continue
            try:
                for child in process.children(recursive=True):
                    scanned.add(child)
                    self._discover_child(child)
            except psutil.NoSuchProcess:
                pass

    def _discover_child(self, child):
        new = child not in self.known
        self.known.setdefault(child, child)
        if self.exit_handles:
            self.exit_handles.adopt(child)
        if new and self.delegation:
            self.delegation.observe(child)
        if new and self.reporter:
            self.reporter.descendant(child)

    def sample(self):
        self.discover()
        processes = []
        for process in list(self.known):
            try:
                if not process.is_running():
                    continue
                memory = process.memory_info()
                processes.append({"pid": process.pid, "created": process.create_time(),
                                  **diagnostic_identity(process),
                                  "rss": memory.rss, "private_commit": getattr(memory, "private", memory.rss)})
            except psutil.NoSuchProcess:
                pass
        return {"processes": processes, "process_count": len(processes),
                "rss": sum(item["rss"] for item in processes),
                "private_commit": sum(item["private_commit"] for item in processes)}

    def native_child_exit(self, evidence):
        for process in list(self.known):
            if process == self.root or process in self.recorded_exits:
                continue
            code = self._exit_code(process)
            if code is None:
                continue
            self._record_exit(process, code, evidence)
            state = self._exit_state(process, code)
            if state == "pending":
                continue
            self.recorded_exits.add(process)
            if state != "complete":
                return "native_child_crash"
            self._retire(process)
        return None

    def _retire(self, process):
        self.known.pop(process)
        # Inner source handles stay retained until the authenticated finished receipt.
        if not self.exit_handles or getattr(self, "reporter", None):
            return
        handles = getattr(self.exit_handles, "handles", {})
        handle = handles.pop(process, None)
        if handle is not None:
            self.exit_handles._close(handle)

    def _exit_code(self, process):
        try:
            return self.exit_handles.poll(process) if self.exit_handles else process.wait(timeout=0)
        except psutil.TimeoutExpired:
            return None

    def _record_exit(self, process, code, evidence):
        self.reported_exits = getattr(self, "reported_exits", set())
        if process not in self.reported_exits:
            evidence.emit("child_exit", pid=process.pid, created=process.create_time(), code=code)
            self.reported_exits.add(process)

    def _exit_state(self, process, code):
        if os.name == "nt" and code & 0x80000000:
            return self.delegation.decision(process, code) if self.delegation else "unknown"
        return "complete"

    def stop(self, evidence):
        self.discover()
        processes = list(self.known)
        # 止住父级再清理已捕获子级；不按进程名/全局 PID 列表找目标。
        for process in processes:
            try:
                if process.is_running():
                    process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(processes, timeout=3)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=3)
        self.terminal_exits(evidence)
        evidence.emit("tree_stopped", remaining_pids=[process.pid for process in alive])
        return alive

    def terminal_exits(self, evidence):
        for process in self.known:
            if process != self.root:
                code = self._exit_code(process)
                if code is not None:
                    self._record_exit(process, code, evidence)

    def close(self):
        if self.exit_handles:
            self.exit_handles.close()


class FatalLogReader:
    def __init__(self, path):
        self.stream = path.open("rb")
        self.tail = b""

    def check(self):
        while chunk := self.stream.read(1024 * 1024):
            text = self.tail + chunk
            if FATAL_OUTPUT.search(text):
                return "fatal_output"
            self.tail = text[-512:]
        return None

    def close(self):
        self.stream.close()


def launch(options, output, job):
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONFAULTHANDLER": "1",
           "PYTHONUNBUFFERED": "1", "QASCADE_GUARD_ACTIVE": "1"}
    delegation = getattr(options, "_delegation_server", None)
    if delegation:
        env.update(delegation.environment())
    flags = 0x00000004 | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = psutil.Popen(options.command, stdin=subprocess.DEVNULL, stdout=output,
                           stderr=subprocess.STDOUT, env=env, creationflags=flags, shell=False)
    try:
        options._launch_process = process
        initial_sample(options, process, job)
        if delegation:
            delegation.set_root(process)
        reporter = client_from_environment()
        if reporter:
            options._delegation_reporter = reporter
            reporter.register(process, options)
        if job:
            job.attach_and_resume(process)
    except BaseException:
        process.kill()
        process.wait(timeout=3)
        raise
    return process


def initial_sample(options, process, job):
    evidence = getattr(options, "_launch_evidence", None)
    if evidence is None:
        return
    memory = process.memory_info()
    private = getattr(memory, "private", memory.rss)
    row = {"pid": process.pid, "created": process.create_time(), **diagnostic_identity(process),
           "rss": memory.rss, "private_commit": private}
    evidence.emit("sample", **system_snapshot(options.log), processes=[row], process_count=1,
                  rss=memory.rss, private_commit=private, elapsed=0, log_bytes=0,
                  phase="suspended_before_resume" if os.name == "nt" else "after_launch",
                  **(job.memory_snapshot() if job else {}))


def monitor(options, tree, job, evidence):
    started = time.monotonic()
    reader = FatalLogReader(options.log)
    try:
        while True:
            snapshot = {**system_snapshot(options.log), **tree.sample(),
                        "elapsed": time.monotonic() - started,
                        "log_bytes": options.log.stat().st_size}
            if job:
                snapshot.update(job.memory_snapshot())
            delegation = getattr(tree, "delegation", None)
            if delegation:
                snapshot["log_bytes"] += delegation.archived_bytes
                delegation.drain(evidence)
            evidence.emit("sample", **snapshot)
            reason = stop_reason(options, tree, snapshot, reader, evidence)
            if reason:
                evidence.emit("stopping", reason=reason, resources=snapshot)
                return stop_result(tree, job, evidence, reason)
            code = tree.root.poll()
            if code is not None:
                return exit_result(tree, job, evidence, code, snapshot)
            time.sleep(options.interval)
    finally:
        reader.close()


def stop_reason(options, tree, snapshot, reader, evidence):
    reason = resource_reason(snapshot, options) or reader.check() or tree.native_child_exit(evidence)
    if snapshot["elapsed"] >= options.timeout:
        reason = reason or "timeout"
    return reason


def stop_result(tree, job, evidence, reason, *, code=None):
    if job:
        job.stop(GUARD_STOP)
    tree.stop(evidence)
    return GUARD_STOP, tree.root.wait(timeout=3) if code is None else code, reason


def exit_result(tree, job, evidence, code, snapshot):
    delegation = getattr(tree, "delegation", None)
    if delegation and delegation.unresolved():
        evidence.emit("stopping", reason="delegation_incomplete", resources=snapshot)
        return stop_result(tree, job, evidence, "delegation_incomplete", code=code)
    # 不遗留验证器的后台子进程；原始 root 非零仍原样返回。
    if job:
        job.close()
    survivors = tree.stop(evidence)
    return (GUARD_STOP if survivors else code), code, "child_exit"


def run_locked(options, evidence):
    before = system_snapshot(options.log)
    evidence.emit("preflight", command=options.command, cwd=str(Path.cwd()),
                  log=str(options.log), launcher_pid=os.getpid(), resources=before,
                  thread_limits={name: os.environ.get(name) for name in BLAS_THREAD_LIMIT_VARS},
                  tool_dependencies={name: version(name) for name in ("psutil", "filelock")},
                  budgets={key: value for key, value in vars(options).items() if key.endswith("gib")})
    reason = resource_reason(before, options, preflight=True)
    if reason:
        evidence.emit("refused", reason=reason)
        return GUARD_STOP
    job = None
    try:
        if os.name == "nt":
            previous = set_local_error_mode()
            evidence.emit("process_error_mode", previous=previous, added_flags=3)
        job = windows_job(int(options.max_gib * GIB))
        return run_child(options, evidence, job)
    finally:
        if job:
            job.close()


def run_child(options, evidence, job):
    tree, started = None, time.monotonic()
    try:
        with options.log.open("xb", buffering=0) as output:
            options._launch_evidence = evidence
            process = launch(options, output, job)
            reporter = getattr(options, "_delegation_reporter", None)
            tree = OwnedTree(process, getattr(options, "_delegation_server", None), reporter)
            evidence.emit("started", pid=process.pid, created=process.create_time(),
                          windows_job=bool(job), stop_conditions=["resource_budget", "fatal_output", "timeout"])
            result, code, reason = monitor(options, tree, job, evidence)
            if reporter and reporter.final_exits(process, evidence) and result == 0:
                result, reason = GUARD_STOP, "native_child_crash"
            record = {"launcher_code": result, "child_code": code,
                      "child_code_hex": f"0x{code & 0xffffffff:08x}", "reason": reason,
                      "elapsed": time.monotonic() - started}
            evidence.emit("finished", **record)
            if reporter:
                reporter.finish({"event": "finished", **record})
            return result
    except (Exception, KeyboardInterrupt) as error:
        evidence.emit("guard_error", error=repr(error))
        failed_run(options, evidence, tree, job, started=started)
        return GUARD_STOP
    finally:
        if tree:
            tree.close()
        reporter = getattr(options, "_delegation_reporter", None)
        if reporter:
            reporter.close()


def failed_run(options, evidence, tree, job, *, started):
    if job and job.handle:
        job.stop(GUARD_STOP)
    try:
        if tree:
            tree.stop(evidence)
    except Exception as error:
        evidence.emit("cleanup_error", error=repr(error))
    process = getattr(options, "_launch_process", None)
    if process is None:
        return
    code = None
    try:
        code = process.wait(timeout=3)
    except Exception as error:
        evidence.emit("raw_exit_unavailable", error=repr(error))
    evidence.emit("finished", launcher_code=GUARD_STOP, child_code=code,
                  child_code_hex=f"0x{code & 0xffffffff:08x}" if code is not None else None,
                  reason="guard_error", elapsed=time.monotonic() - started)


def main(argv=None):
    options = parse_args(argv)
    options.log.parent.mkdir(parents=True, exist_ok=True)
    evidence = Evidence(options.log)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(LOCK_PATH), timeout=0):
            return run_with_delegation(options, evidence)
    except Timeout:
        evidence.emit("refused", reason="another_heavy_validation_holds_lock", lock=str(LOCK_PATH))
        return LOCK_BUSY
    except (OSError, psutil.Error) as error:
        evidence.emit("guard_error", error=repr(error))
        return GUARD_STOP
    finally:
        evidence.close()


def run_with_delegation(options, evidence):
    server = DelegationServer(options) if os.name == "nt" else None
    options._delegation_server = server
    try:
        return run_locked(options, evidence)
    finally:
        if server:
            server.close()
            server.drain(evidence)


if __name__ == "__main__":
    raise SystemExit(main())
