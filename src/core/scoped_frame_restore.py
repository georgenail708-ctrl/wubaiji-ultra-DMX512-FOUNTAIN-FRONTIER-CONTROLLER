"""Select frame sidecars by undo domain without encoding unchanged recordings.

Project.load projects the active program into live tracks. Historical flat
sidecars keep their original live-only attachment; full payloads own their frames.
"""


def merged_frame_refs(domain, current, snapshot):
    """Use historical recordings only for containers restored by this action."""
    if not (isinstance(snapshot, tuple) and len(snapshot) == 2):
        snapshot = ([], snapshot or [])
    programs = snapshot[0] if "programs" in domain else current[0]
    live = snapshot[1] if domain.intersection({"tracks", "programs"}) else current[1]
    return programs, live
