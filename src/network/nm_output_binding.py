"""Ordinary NM control seams, reusing its RLock and the verified output tap.

No sender, engine, frame history or payload cache. Original startup/project
work is moved once into short phases. None never creates optional sessions.
ContextVar/dataclass and original XYReference value semantics are reused;
CPython references and licenses.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

import numpy as np

from ..core.models import Protocol
from ..core.project_frame_origin import bind_project_frame
from ..engine.xy_reference import XYReference
from .artnet_node import ReceiverBindError
from .nm_final_output_tap import NMFinalOutputTap
from .nm_output_delivery import create_nm_output_router


@dataclass(slots=True, eq=False, kw_only=True)
class _BindingSession:
    binding: object
    kind: str
    lease: object
    collision: int
    parent: object
    project: object = None
    failed: bool = False
    closed: bool = False
    token: object = None


_CURRENT_BINDING = ContextVar("qascade_nm_binding_session", default=None)


def _restore_binding_session(session):
    try:
        _CURRENT_BINDING.reset(session.token)
    except Exception:
        try:
            _CURRENT_BINDING.set(session.parent)
        except Exception:
            pass
    finally:
        # Completed control calls must not retain another NM/project via a token.
        session.token = session.parent = session.project = None


class NMOutputBinding:
    """Control-only latch; all shared fields are protected by NM's old lock."""

    def __init__(self, nm, tap):
        self.nm, self.tap = nm, tap
        self.projects, self.collision = 0, 0
        self.project_ok, self.network_ok = True, False
        self.latest = None

    def _joins(self, parent, kind, project):
        if parent is None or parent.binding is not self or parent.closed:
            return False
        if parent.kind == "project":
            return kind in ("defaults", "map") or (
                kind == "xy" and project is parent.project)
        return parent.kind in ("xy", "map") and kind == "map"

    def begin(self, kind, project):
        with self.nm._state_lock:
            parent = _CURRENT_BINDING.get()
            if self._joins(parent, kind, project):
                return None
            overlap = self.projects > 0
            if overlap:
                self.collision += 1
                self.project_ok = False
            session = _BindingSession(
                binding=self, kind=kind, lease=self.tap.begin_binding(),
                collision=self.collision, parent=parent, project=project, failed=overlap)
            session.token = _CURRENT_BINDING.set(session)
            if kind == "project":
                self.projects += 1
            self.latest = session
            return session

    def _valid(self, session, success):
        return (success and not session.failed and self.latest is session
                and session.collision == self.collision)

    def _ready(self, session, valid):
        parent = session.parent
        nested = parent is not None and parent.binding is self and not parent.closed
        return (valid and self.network_ok and self.project_ok and self.projects == 0
                and self.nm._running and not nested)

    def finish(self, session, success):
        if session.closed:
            return
        session.closed = True
        try:
            with self.nm._state_lock:
                valid = self._valid(session, success)
                self._finish_latches(session, valid)
                self.tap.commit_binding(
                    session.lease, protocol=self.nm._config.protocol.value,
                    universes=self.nm._output_universe_allowlist(),
                    ready=self._ready(session, valid))
        finally:
            _restore_binding_session(session)

    def _finish_latches(self, session, valid):
        if session.kind == "project":
            self.projects -= 1
            self.project_ok = valid
        if not valid and session.kind in ("defaults", "xy", "map", "z"):
            self.project_ok = False
        if session.kind in ("start", "rebuild") and self.latest is session:
            self.network_ok = valid

    def invalidate(self):
        with self.nm._state_lock:
            self.latest, self.network_ok = None, False
            if self.projects:
                self.collision += 1
                self.project_ok = False
            self.tap.invalidate()


def initialize_output_services(nm, observer, timer_type):
    nm._mixer_timer = timer_type(nm)
    nm._mixer_timer.setInterval(int(nm._mix_interval_sec * 1000))
    nm._mixer_timer.timeout.connect(nm._mix_and_send)
    if observer is None:
        return
    try:
        with nm._state_lock:
            tap = NMFinalOutputTap(observer, protocol=nm._config.protocol.value,
                                   universes=nm._output_universe_allowlist(), ready=False)
            binding = NMOutputBinding(nm, tap)
            nm._final_output_tap, nm._final_output_binding = tap, binding
    except Exception:
        pass  # Optional installation cannot prevent original NM construction.


def final_output_reader(nm):
    tap = nm._final_output_tap
    return None if tap is None else tap.reader()


def begin_nm_binding(nm, kind, *, project=None):
    binding = nm._final_output_binding
    if binding is None:
        return None
    try:
        return binding.begin(kind, project)
    except Exception:
        invalidate_nm_binding(nm)
        return None


def finish_nm_binding(nm, session, *, success=True):
    if session is None:
        return
    try:
        session.binding.finish(session, success)
    except Exception:
        invalidate_nm_binding(nm)


def invalidate_nm_binding(nm):
    binding = nm._final_output_binding
    if binding is None:
        return
    try:
        binding.invalidate()
    except Exception:
        pass


def mark_nm_binding_failed(nm):
    binding = nm._final_output_binding
    if binding is None:
        return
    try:
        with nm._state_lock:
            session = _CURRENT_BINDING.get()
            if session is not None and session.binding is binding:
                session.failed = True
                if session.kind in ("project", "defaults", "xy", "map", "z"):
                    binding.project_ok = False
    except Exception:
        invalidate_nm_binding(nm)


def before_nm_binding_mutation(nm):
    """A late old control body may run, but cannot borrow a newer ready state."""
    binding = nm._final_output_binding
    if binding is None:
        return
    try:
        with nm._state_lock:
            session = _CURRENT_BINDING.get()
            if session is not None and session.binding is binding:
                if binding.latest is not session:
                    binding.project_ok = False
                    binding.collision += 1
                    binding.latest = None
                    binding.tap.invalidate()
    except Exception:
        invalidate_nm_binding(nm)


def _known_map(refs):
    return all(type(ref) is XYReference for values in refs.values() for ref in values)


def begin_nm_map_binding(nm, refs):
    if nm._final_output_binding is None:
        return None
    try:
        candidate = refs or {}
        if _known_map(candidate) and candidate == nm._xy_reference_by_universe:
            return None
    except Exception:
        pass
    return begin_nm_binding(nm, "map")


def apply_channel_defaults_locked(nm, new_defaults):
    """Accept prepared defaults once; caller holds NM's original state lock."""
    session = begin_nm_binding(nm, "defaults")
    success = False
    try:
        before_nm_binding_mutation(nm)
        old_universes = set(getattr(nm, "_channel_defaults", {}).keys())
        nm._channel_defaults = new_defaults
        nm._direct_anchor_by_universe = {}
        for u, (data, active) in new_defaults.items():
            anchor = nm._shape_default_anchor.copy()
            anchor[active] = data[active].astype(np.float32)
            nm._direct_anchor_by_universe[int(u)] = anchor
        nm._reset_output_smoother_states()
        for u in old_universes | set(new_defaults.keys()):
            nm._mark_dirty_universe(int(u))
        success = True
    finally:
        finish_nm_binding(nm, session, success=success)


def _start_senders(nm, cfg, input_enabled, receiver_type):
    if cfg.protocol == Protocol.ARTNET:
        nm._start_artnet(cfg, input_enabled)
        return
    if input_enabled:
        nm._sacn_rx = receiver_type(cfg.input_ip)
        nm._sacn_rx.frame_received.connect(nm._on_raw_frame_sacn)
        nm._sacn_rx.start()
    nm._output_allowlist_cache = None
    for universe in sorted(nm._output_universe_allowlist()):
        nm._get_or_create_sacn_sender(universe)


def _start_router_watch(nm, router_type, watch_type):
    if nm._use_output_router:
        nm._output_router = create_nm_output_router(nm, router_type)
        nm._output_router.start()
    if nm._nic_watch is None:
        nm._nic_watch = watch_type(nm)
        nm._nic_watch.nics_changed.connect(nm._on_nics_changed)
    nm._nic_watch.start()


def configure_nm(nm, config, logger):
    with nm._state_lock:
        session = begin_nm_binding(nm, "configure")
        success = False
        try:
            previous_domains = nm._output_universe_allowlist() if (running := nm.__dict__.get("_running", False)) else None
            nm._config = config
            nm._output_allowlist_cache = None
            # A10 S1/G16: next frame changes after a routing configuration.
            nm._last_sent_snapshot.clear()
            domains_changed = running and previous_domains != nm._output_universe_allowlist()
            success = True
        finally:
            finish_nm_binding(nm, session, success=success)
        if domains_changed:
            nm._mark_all_known_universes_dirty()
    logger.info("网络配置已更新: %s", config.protocol.value)


def _publish_started(nm, session):
    with nm._state_lock:
        nm._running = True
        nm._dirty_universes.clear()
        nm._flush_scheduled = False
        nm._last_heartbeat_send = 0.0
        nm._reset_change_baseline_locked()
        finish_nm_binding(nm, session)
        nm._mark_all_known_universes_dirty()
        if nm._output_worker_enabled:
            nm._start_output_worker_locked()
        else:
            nm._mixer_timer.start()


def start_nm(nm, receiver_type, watch_type, router_type, logger):
    with nm._state_lock:
        if nm._running:
            return
        session = begin_nm_binding(nm, "start")
        for layer_name in nm._layers:
            nm._layers[layer_name].clear()
        nm._reset_output_smoother_states()
        nm._last_bind_error = None
    cfg = nm._config
    input_enabled = bool(getattr(cfg, "input_enabled", False)) or nm._read_bool_env(
        "QASCADE_DMX_INPUT", False)
    nm._sacn_tx_pool.clear()  # Original R183 F2 position before protocol setup.
    try:
        _start_senders(nm, cfg, input_enabled, receiver_type)
        if not input_enabled:
            logger.info("DMX 输入接收器未启动（输出专用模式；如需监听请在网络配置中启用输入）")
        _start_router_watch(nm, router_type, watch_type)
        _publish_started(nm, session)
        nm.status_changed.emit(f"运行中 [{cfg.protocol.value}]")
    except ReceiverBindError as error:
        nm._on_receiver_bind_error(error)
    except Exception:
        logger.exception("NetworkManager.start() 失败")
        nm.stop()
        raise
    finally:
        finish_nm_binding(nm, session, success=False)


def _fixture_defaults(fi, fd, defaults, channel_key, idle_default):
    channels = list(getattr(fd, "channels", []) or []) if fd is not None else []
    universe = int(getattr(fi, "universe", 1) or 1)
    start = int(getattr(fi, "start_channel", 1) or 1) - 1
    for offset, ch_type in enumerate(channels):
        ch_type_s = channel_key(ch_type)
        address = start + int(offset)
        if not 0 <= address < 512:
            continue
        value = idle_default(fd, ch_type_s)
        if value is not None:
            defaults.setdefault(universe, {})[address] = value


def _collect_defaults(project, defaults, channel_key, idle_default):
    fdefs = {str(getattr(fd, "id", "")): fd
             for fd in (getattr(project, "fixture_defs", []) or [])
             if getattr(fd, "id", None)}
    for fi in (getattr(project, "fixtures", []) or []):
        fd = fdefs.get(str(getattr(fi, "def_id", "")))
        _fixture_defaults(fi, fd, defaults, channel_key, idle_default)


def _apply_fixture_defaults(nm, project, channel_key, idle_default, logger):
    _set_project_caches(nm, project)
    defaults = {}
    if project is not None:
        try:
            _collect_defaults(project, defaults, channel_key, idle_default)
        except Exception:
            mark_nm_binding_failed(nm)
            logger.exception("set_fixture_defaults_from_project failed")
    nm.set_channel_defaults(defaults)
    nm.set_xy_reference_from_project(project)


def _set_project_caches(nm, project):
    with nm._state_lock:
        before_nm_binding_mutation(nm)
        _set_project_cache_fields(nm, project)


def _set_project_cache_fields(nm, project):
    bind_project_frame(nm, project)
    nm._motor_pair_groups_sig = None
    nm._motor_pair_groups_cache = {}
    nm._owned_table_sig = None
    nm._owned_table_cache = {}
    nm._output_allowlist_cache = None


def apply_fixture_defaults(nm, project, channel_key, idle_default, logger):
    session = begin_nm_binding(nm, "project", project=project)
    success = False
    try:
        _apply_fixture_defaults(nm, project, channel_key, idle_default, logger)
        success = True
    finally:
        finish_nm_binding(nm, session, success=success)


def _legacy_xy_project(nm, project, builder, logger):
    with nm._state_lock:
        before_nm_binding_mutation(nm)
        bind_project_frame(nm, project)
    try:
        nm.set_xy_reference_map(builder(project))
    except Exception:
        mark_nm_binding_failed(nm)
        logger.exception("set_xy_reference_from_project failed")
        nm.set_xy_reference_map({})


def _same_xy_project(nm, project, builder, logger):
    try:
        refs = builder(project)
    except Exception:
        session = begin_nm_binding(nm, "xy", project=project)
        try:
            mark_nm_binding_failed(nm)
            logger.exception("set_xy_reference_from_project failed")
            nm.set_xy_reference_map({})
        finally:
            finish_nm_binding(nm, session, success=False)
        return
    with nm._state_lock:
        session = begin_nm_map_binding(nm, refs)
    success = False
    try:
        nm.set_xy_reference_map(refs)
        success = True
    finally:
        finish_nm_binding(nm, session, success=success)


def set_xy_from_project(nm, project, builder, logger):
    binding = nm._final_output_binding
    if binding is None:
        return _legacy_xy_project(nm, project, builder, logger)
    parent = _CURRENT_BINDING.get()
    if binding._joins(parent, "xy", project):
        return _legacy_xy_project(nm, project, builder, logger)
    if project is nm._project_ref:
        return _same_xy_project(nm, project, builder, logger)
    session = begin_nm_binding(nm, "xy", project=project)
    success = False
    try:
        _legacy_xy_project(nm, project, builder, logger)
        success = True
    finally:
        finish_nm_binding(nm, session, success=success)
