"""Actual physical local ownership; source-only unmounted LOCK4 candidate."""
from contextvars import ContextVar
import threading

from .timeline_temporal_replay import outer_owns_lock, claim_outer_lock
from .lock4_admission import source_current


_LOCAL = ContextVar('qascade_actual_local_render_owner', default=None)


class _Lease:
    def __init__(self, engine, bridge):
        self.engine, self.bridge = engine, bridge
        self.ident = threading.get_ident  # Retain validated builtin for cleanup.
        self.thread = self.ident()
        self.physical_lock = engine._render_lock
        self.frame_borrowed = False


def _actual_lease(engine):
    lease = _LOCAL.get()
    if (type(lease) is not _Lease or lease.engine is not engine
            or lease.thread != lease.ident()):
        return None
    return lease


def has_local_owner(engine):
    # A replaced engine field must not erase a genuine outstanding loan. The
    # borrowing path rejects replacement; the returning path keeps the lease.
    return _actual_lease(engine) is not None


class _Transaction:
    def __init__(self, engine, bridge):
        self.engine, self.bridge = engine, bridge
        self.lease = self.token = self.physical = None

    def __enter__(self):
        engine, bridge = self.engine, self.bridge
        if not source_current(bridge):
            raise RuntimeError('LOCK4 local transaction source is not admitted')
        if _LOCAL.get() is not None:
            lease = _actual_lease(engine)
            if (lease is None or lease.bridge is not bridge or lease.frame_borrowed
                    or lease.physical_lock is not engine._render_lock
                    or outer_owns_lock(engine)):
                raise RuntimeError('Local mutation cannot reenter an active render owner')
            return self
        if outer_owns_lock(engine):
            raise RuntimeError('Local mutation cannot reenter an active render owner')
        physical = engine._render_lock
        if not physical.acquire(timeout=0.2):
            raise RuntimeError('时间线正在处理一帧，本次本地操作尚未执行')
        try:
            lease = _Lease(engine, bridge)
            if lease.physical_lock is not physical or not source_current(bridge):
                raise RuntimeError('Local render ownership changed while acquiring')
            self.lease, self.physical = lease, physical
            self.token = _LOCAL.set(lease)
            return self
        except BaseException:
            physical.release()
            raise

    def __exit__(self, error_type, error, traceback):
        if self.lease is None:
            return False
        _LOCAL.reset(self.token)
        try:
            if self.lease.frame_borrowed or outer_owns_lock(self.engine):
                raise RuntimeError('Original render did not return its local lock loan')
        finally:
            self.physical.release()
        return False


def local_render_transaction(engine, bridge):
    return _Transaction(engine, bridge)


def borrow_local_frame(engine):
    """Only a real acquired local transaction may start one original frame."""
    lease = _actual_lease(engine)
    if lease is None or lease.frame_borrowed or outer_owns_lock(engine):
        return False
    if lease.physical_lock is not engine._render_lock:
        raise RuntimeError('Local physical render lock changed before borrowing')
    lease.frame_borrowed = True
    try:
        claim_outer_lock(engine)
    except BaseException:
        lease.frame_borrowed = False
        raise
    return True


def return_local_frame(engine):
    """Called after ALL original ready-finalization and outer retirement."""
    # Cleanup belongs to the actually acquired physical lease even if an
    # original signal callback replaced engine._render_lock in the meantime.
    lease = _actual_lease(engine)
    if lease is None:
        return False
    if not lease.frame_borrowed or outer_owns_lock(engine):
        raise RuntimeError('Invalid actual local frame return')
    lease.frame_borrowed = False
    return True
