"""Original public candidate arbitration, shared by normal and pure fallback.

Keep all tied effective rows, first local winner, explicit active zero and
finite-score filtering. Native prepared arbitration is checked against this.
"""
import numpy as np


def _resolve_single(candidate):
    data, active, anchor, scores = candidate
    winner = np.full(512, -1, np.int16)
    effective = active & np.isfinite(scores)
    if not effective.any():
        return [], winner
    winner[effective] = 0
    owned = data.copy()
    owned[~effective] = 0
    return [(owned, effective, anchor)], winner


def _best_scores(candidates):
    best = np.full(512, -np.inf, np.float64)
    masks = []
    for _data, active, _anchor, scores in candidates:
        mask = active & np.isfinite(scores)
        masks.append(mask)
        if mask.any():
            best[mask] = np.maximum(best[mask], scores[mask])
    return best, masks


def _resolve_group(candidates):
    if len(candidates) == 1:
        return _resolve_single(candidates[0])
    winner = np.full(512, -1, np.int16)
    best, masks = _best_scores(candidates)
    layers = []
    for index, ((data, _active, anchor, scores), mask) in enumerate(zip(candidates, masks)):
        if not mask.any():
            continue
        effective = mask & (scores >= best - 1e-12)
        if not effective.any():
            continue
        winner[effective & (winner == -1)] = index
        owned = data.copy()
        owned[~effective] = 0
        layers.append((owned, effective, anchor))
    return layers, winner


def resolve_python_candidates(candidate_map):
    resolved, winners = {}, {}
    for universe, candidates in candidate_map.items():
        if not candidates:
            continue
        layers, winner = _resolve_group(candidates)
        winners[int(universe)] = winner
        if layers:
            resolved[int(universe)] = layers
    return resolved, winners
