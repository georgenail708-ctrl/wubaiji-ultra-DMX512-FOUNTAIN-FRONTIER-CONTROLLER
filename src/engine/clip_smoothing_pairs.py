"""Project-defined physical coarse/fine pairs; no inferred motor byte semantics.

Reuse the existing fixture-table projection, including repeated channel order.
Only pairs with both bytes inside this clip's actual mask are processed together.
"""
from ..processing.dispatch.fixture_table import build_fixture_info


def selected_pairs(project, masks):
    if project is None:
        return {}
    definitions = {str(row.id): row for row in project.fixture_defs}
    fixtures = build_fixture_info(project.fixtures, definitions)
    pairs = {}
    for fixture in fixtures.values():
        _append_fixture_pairs(fixture, masks, pairs)
    return {universe: sorted(rows) for universe, rows in pairs.items()}


def _append_fixture_pairs(fixture, masks, pairs):
    addresses = fixture["channel_addrs"]
    for key, coarse_rows in addresses.items():
        if key.endswith("_fine"):
            continue
        for coarse, fine in zip(coarse_rows, addresses.get(key + "_fine", ())):
            if coarse[0] != fine[0]:
                continue
            universe, channel = coarse
            selected = masks.get(universe, masks.get(str(universe), ())) if masks else None
            if selected is None or (channel + 1 in selected and fine[1] + 1 in selected):
                pairs.setdefault(universe, set()).add((channel, fine[1]))
