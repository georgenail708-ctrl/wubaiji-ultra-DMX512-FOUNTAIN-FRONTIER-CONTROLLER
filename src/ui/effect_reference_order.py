"""Keep reference order metadata beside the original Effect compute order.

The compute sequence is supplied by each existing caller and never projected
here. References describe the mother group; they do not expand output targets.
"""


def _fields(compute, reference):
    return {
        "base_order_ids": list(compute),
        "reference_base_order_ids": list(reference or compute),
    }


def play_fields(params, compute, normalize):
    """Read the caller's mother order before transport keys leave params."""
    reference = normalize((params or {}).get("_output_base_order_ids"))
    return _fields(compute, reference)


def reference_ids(state, normalize):
    """Modern metadata first, with the original legacy compute-order fallback."""
    reference = normalize(state.get("reference_base_order_ids"))
    return reference or normalize(state.get("base_order_ids"))


def save_fields(state):
    """Copy both white-listed lists without changing legacy compute values."""
    compute = list(state.get("base_order_ids", []) or [])
    reference = list(state.get("reference_base_order_ids", []) or [])
    return _fields(compute, reference)


def load_fields(state, compute, normalize):
    return _fields(compute, reference_ids(state, normalize))


def recall_fields(node, entry, compute, normalize):
    """Use the saved node's mother order, never the current author's scope."""
    reference = normalize(node.get("base_ids"))
    if not reference:
        reference = reference_ids(entry or {}, normalize)
    return _fields(compute, reference)


def follow_fields(base):
    """Manual selection follow retains its existing mother-order computation."""
    return _fields(base, base)
