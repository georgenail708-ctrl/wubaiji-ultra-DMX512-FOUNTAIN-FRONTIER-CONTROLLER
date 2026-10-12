"""Per-call, strong-owned hold metadata; channel math stays in the native domain."""
from ..core.native_mask_math import domain


class UniverseHoldIndex:
    def __init__(self, rows, metas):
        self.rows = rows
        self.groups = {}
        self.group_identities = {}
        self.first = {}
        self.first_groups = {}
        for index, (row, meta) in enumerate(zip(rows, metas)):
            group = tuple(meta[1] or ())
            identity = id(row)
            self.groups[group] = self.groups.get(group, 0) + 1
            pair = (group, identity)
            self.group_identities[pair] = self.group_identities.get(pair, 0) + 1
            self.first.setdefault(identity, index)
            self.first_groups.setdefault(identity, group)

    def query(self, candidate, chain):
        group = tuple(chain or ())
        count = self.groups.get(group, 0)
        if candidate is None:
            return count > 0, None, True
        identity = id(candidate)
        index = self.first.get(identity)
        if index is None or self.rows[index] is not candidate:
            return False, None, False
        if self.first_groups[identity] != group:
            return False, None, False
        return count > self.group_identities.get((group, identity), 0), index, True


def hold_metadata_queries(maps, registry):
    """Preflight every query before any original consumer state can change.

    An absent tuple or inconsistent identity/group has no corresponding native
    hold result. Reject the whole bridge so the original consumer runs once.
    """
    candidates, metas = maps
    indices = {universe: UniverseHoldIndex(rows, metas.get(universe, []))
               for universe, rows in candidates.items()}
    row_count = sum(len(index.rows) for index in indices.values())
    empty = UniverseHoldIndex([], [])
    result = []
    for universe, key, candidate, chain in registry:
        has_peer, index, valid = indices.get(universe, empty).query(candidate, chain)
        if not valid:
            _note(row_count, len(result) + 1, unsupported=1)
            return None
        result.append((universe, key, has_peer, index))
    _note(row_count, len(result))
    return result


def _note(rows, queries, unsupported=0):
    module = domain()
    if module is not None:
        module.note_hold_metadata(rows, queries, unsupported)
