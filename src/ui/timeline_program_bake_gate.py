"""Keep a pure program-list refresh from retiring completed timeline frames.

Reuse Main's existing AutoBaker invalidation and program ownership. Retain one
project reference and compare identity, as the original project-frame lease does;
Project equality traverses mutable arrays and an id alone can later be reused.
"""


def update_program_bake_gate(window, baker, active):
    project = getattr(getattr(window, "_pm", None), "project", None)
    current = (project, str(getattr(project, "current_program_id", "") or ""), bool(active))
    previous = getattr(window, "_timeline_bake_program_gate", None)
    changed = (previous is None or previous[0] is not project
               or previous[1:] != current[1:])
    window._timeline_bake_program_gate = current  # Inactive transitions count too.
    if active and changed:
        baker.schedule_invalidate()
