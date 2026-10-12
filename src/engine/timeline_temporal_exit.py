"""Pure compatibility metadata bridge; one causal journal/restore implementation."""
from .temporal_journal import capture_domains
from .transition_anchor_causality import capture_anchor_state


def flatten_complete(complete):
    return dict(complete["temporal"], transition_anchors=complete["transition_anchors"])


def expand_flat(point):
    return {"temporal": {name: point[name] for name in ("state", "rule_history", "last_time")},
            "transition_anchors": point["transition_anchors"]}


def capture_complete_current(engine):
    return {"temporal": capture_domains(engine, current_latches=engine._hold_boundary_latch),
            "transition_anchors": capture_anchor_state(engine)[1]}
