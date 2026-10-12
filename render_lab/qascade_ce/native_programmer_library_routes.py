"""Default route dispatch; function/class owners remain the original authority."""
from . import native_programmer_library_curve as curves
from . import native_programmer_library_groups as groups
from . import native_programmer_library_evaluate as evaluation


def curve_route(native, name, arguments):
    source = arguments["self"]
    if name == "segment_progress_at":
        return curves.progress(native, source, arguments["t"])
    if name == "shape_boundary_windows":
        return curves.windows(native, source)
    if name == "_shape_segment_field":
        return curves.shape_field(native, source, arguments)
    if name == "_evaluate_path":
        return evaluation.path(native, source, arguments["t"],
                               arguments["resolve_group_segments"], arguments["positions"])
    return evaluation.shape(native, source, arguments)


def segment_route(native, name, arguments):
    owner = arguments["self"]
    if name == "effective_batches":
        return groups.effective(native, owner)
    offsets, span = groups.offsets(native, owner, arguments["per_group"],
                                   name == "sequential_span_seconds")
    return offsets if name == "batch_offsets_seconds" else span


def shared_route(native, name, arguments):
    if name == "_sort_by_direction":
        return groups.direction(native, arguments["ids"], arguments["direction"],
                                arguments["positions"])
    if name == "_batch_partition":
        return groups.partition(native, arguments["n_units"], arguments["batch_count"])
    return groups.reveal(native, arguments) if name == "reached_fixture_ids" else groups.morph(native, arguments)
