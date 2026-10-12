"""Save existing programmed history to a Project independently of author S/M.

Values still come from original marked/static rows; no remembered scope or
parallel ledger. Scene captures and real clear/reset keep their existing rules.
"""
from __future__ import annotations


def capture_project_history(panel, state: dict) -> dict:
    from .programmer_panel import _def_ids_for, _scope_range_states, _scope_static_values

    saved = dict(state)
    project = panel._project_data()
    valid = {str(fixture.id) for fixture in project.fixtures}
    values = _scope_static_values(panel.snapshot_static_values(), valid)
    candidates = list(saved.get("group_ids") or []) + list(values)
    targets = list(dict.fromkeys(str(fid) for fid in candidates if str(fid) in valid))
    saved["static_values"] = values
    saved["group_ids"] = targets
    saved["range_states"] = _scope_range_states(
        panel._collect_range_states(), _def_ids_for(panel, targets))
    return saved
