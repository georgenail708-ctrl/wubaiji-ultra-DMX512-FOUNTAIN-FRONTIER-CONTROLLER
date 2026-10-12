"""Optional preview control; reuse NM lock, binding, tap and baseline reset.

Adoption evidence: r185-basic-closeout-preview.md (CLOSE-P).
"""
from .final_output_observer import FinalOutputObserver
from .nm_final_output_tap import NMFinalOutputTap
from .nm_output_binding import NMOutputBinding


def final_output_preview_enabled(nm):
    tap = nm._final_output_tap
    return tap is not None and tap.control is not None and tap.control.enabled


def set_final_output_preview_enabled(nm, enabled):
    """Clear old epoch and request a fresh attempt via the original pipeline."""
    with nm._state_lock:
        tap = nm._final_output_tap
        if tap is None:
            if not enabled:
                return True
            observer = FinalOutputObserver()
            tap = NMFinalOutputTap(observer, protocol=nm._config.protocol.value,
                                   universes=nm._output_universe_allowlist(), ready=False)
            binding = NMOutputBinding(nm, tap)
            binding.network_ok = bool(nm._running)
            nm._final_output_tap, nm._final_output_binding = tap, binding
        if bool(tap.reader().enabled) == bool(enabled):
            return True
        binding = nm._final_output_binding
        tap.set_enabled(enabled=bool(enabled))
        router = nm._output_router
        if router is not None:
            router.configure_observation(
                record_sink=tap.after_record if enabled else None,
                attempt_scope=tap.attempt_scope if enabled else None)
        # set_enabled creates its own lease; commit a new explicit current one.
        lease = tap.begin_binding()
        ready = (bool(nm._running) and binding.network_ok
                 and binding.project_ok and binding.projects == 0)
        result = tap.commit_binding(lease, protocol=nm._config.protocol.value,
                                   universes=nm._output_universe_allowlist(), ready=ready)
        if enabled:
            nm._reset_change_baseline_locked()
            nm._mark_all_known_universes_dirty()
        return result
