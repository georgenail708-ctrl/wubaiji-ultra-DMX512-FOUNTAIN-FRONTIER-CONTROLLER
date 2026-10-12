"""Exact anchor dependency invalidation without formatting whole timeline keys.

Reuse the original dependency map and equality predicate. Tuple indexes retain
the original object so Python id reuse cannot match a different cache key.
"""


def _strings(value, memo):
    cached = memo.get(id(value)) if isinstance(value, tuple) else None
    if cached is not None and cached[1] is value:
        return cached[0]
    result = frozenset(_string_leaves(value))
    if isinstance(value, tuple) and len(memo) < 4096:
        memo[id(value)] = (result, value)
    return result


def _string_leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (tuple, list, set)):
        for part in value:
            yield from _string_leaves(part)


def _static_strings(signature, memo):
    """Static fields 0/1/4 are numeric frame counts, universes and channel lists."""
    if not isinstance(signature, tuple) or len(signature) != 5:
        return _strings(signature, memo)
    return _strings(signature[2], memo) | _strings(signature[3], memo)


def _context_strings(context, memo):
    return (_static_strings(context[1], memo) | _strings(context[2], memo)
            | _strings(context[3], memo))


def _candidate_strings(candidates, memo):
    result = frozenset()
    for row in candidates:
        result |= _strings(row[:4], memo) | _static_strings(row[4], memo) | _strings(row[6], memo)
    return result


def _anchor_strings(key, memo):
    """Candidate field 5 is the original numeric frame-content signature.

    Its (universe, time, sum, weighted_sum) rows contain no clip identity. Avoid
    walking every recorded frame while retaining all other exact string leaves.
    Unknown/legacy key shapes still use the complete original predicate.
    """
    context, candidates = key[3], key[4]
    if not isinstance(context, tuple) or len(context) != 4:
        return _strings(key, memo)
    if not isinstance(candidates, tuple) or not all(
            isinstance(row, tuple) and len(row) == 7 for row in candidates):
        return _strings(key, memo)
    return _strings(key[:3], memo) | _context_strings(context, memo) | _candidate_strings(candidates, memo)


def invalidate_fade_anchors(engine, clip_id):
    clip_id = str(clip_id or "")
    from .transition_anchor_causality import clear_anchor_state
    clear_anchor_state(engine, clip_id)
    anchors = engine._fade_in_anchor
    targets = _dependency_targets(engine, clip_id)
    memo = getattr(engine, "_fade_anchor_key_strings", {})
    if len(memo) >= 4096:
        memo.clear()
    engine._fade_anchor_key_strings = memo
    for key in list(anchors):
        if _affected(key, clip_id, targets, memo):
            anchors.pop(key, None)


def _dependency_targets(engine, clip_id):
    anchors = engine._fade_in_anchor
    dependencies = getattr(engine, "_fade_in_anchor_deps", {})
    targets = set()
    for key, deps in list(dependencies.items()):
        if (isinstance(key, tuple) and key and str(key[0]) == clip_id) or clip_id in (deps or set()):
            target, universe = str(key[0]), int(key[1])
            targets.add(target)
            anchors.pop((target, "fade_in_anchor_ready", universe), None)
            anchors.pop((target, "fade_in_anchor_ready_context", universe), None)
            dependencies.pop(key, None)
    return targets


def _affected(key, clip_id, targets, memo):
    if not isinstance(key, tuple) or not key:
        return str(key).startswith(clip_id)
    if str(key[0]) == clip_id or (len(key) > 4 and isinstance(key[4], str) and key[4] == clip_id):
        return True
    if str(key[0]) in targets and len(key) >= 3 and "fade_in_anchor" in key[:3]:
        return True
    return clip_id in _key_strings(key, memo)


def _key_strings(key, memo):
    if len(key) == 5 and key[1] == "fade_in_anchor":
        return _anchor_strings(key, memo)
    return _strings(key, memo)


def evict_fade_anchor_cache(engine):
    if (
        len(engine._fade_in_anchor) > 4096
        and not bool(getattr(engine, "_timeline_render_cache_building_lock", False))
    ):
        stale_key = next(iter(engine._fade_in_anchor))
        engine._fade_in_anchor.pop(stale_key)
