"""Read current sender destinations without treating pending settings as live."""
from __future__ import annotations

from copy import copy

from ...core.models import Protocol


def active_output_config(nm):
    """Return a detached view of the running senders, or None when stopped."""
    if nm is None or getattr(nm, "_state_lock", None) is None:
        return None
    with nm._state_lock:
        return _snapshot_output_config(nm)


def _snapshot_output_config(nm):
    if not nm._running:
        return None
    cfg = copy(nm._config)
    artnet = nm._artnet_tx
    senders = {u: s for u, s in nm._sacn_tx_pool.items() if s is not None}
    receiver = nm._artnet_rx or nm._sacn_rx
    cfg._runtime_input = str(getattr(receiver, "bind_ip", "0.0.0.0")) if receiver else "未启用"
    if artnet is not None:
        endpoints = list(artnet._targets)
        cfg.protocol = Protocol.ARTNET
        cfg.output_mode = "broadcast" if len(endpoints) > 1 or endpoints[0][0] == "255.255.255.255" else "unicast"
        cfg._actual_targets = [ip for ip, _ in endpoints]
        cfg.output_ip, cfg.output_port = endpoints[0]
    elif senders:
        cfg.protocol = Protocol.SACN
        cfg.output_mode = "broadcast" if next(iter(senders.values())).multicast else "unicast"
        cfg.output_universes = list(senders)
        cfg._actual_targets_by_universe = {u: [s.target_ip] for u, s in senders.items()}
        endpoints = list(dict.fromkeys((s.target_ip, 5568) for s in senders.values()))
        cfg.output_ip, cfg.output_port = endpoints[0]
    else:
        endpoints = []
        cfg._actual_targets = []
    cfg._runtime_endpoints = endpoints
    return cfg


def output_summary(cfg) -> str:
    if cfg is None:
        return ""
    endpoints = getattr(cfg, "_runtime_endpoints", ())
    if not endpoints:
        return ""
    mode = "单播" if cfg.output_mode == "unicast" else (
        "多播" if cfg.protocol == Protocol.SACN else "广播")
    targets = ", ".join(f"{ip}:{port}" for ip, port in endpoints[:3])
    more = f" 等 {len(endpoints)} 个目标" if len(endpoints) > 3 else ""
    return f"{mode} -> {targets}{more}"
