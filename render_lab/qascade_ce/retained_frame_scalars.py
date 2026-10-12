"""Original retained-task metadata feeds one actual native scalar report."""
from . import native_source_motion_clock as clock


def table_scalars(clip, metadata, input_direction, output_direction):
    names = ("priority", "track_order", "start", "local", "hold", "total_priority")
    payload = dict(zip(names, metadata, strict=True))
    fields = {"duration": "duration", "fade_in": "fade_in", "fade_out": "fade_out",
              "delay_in": "seq_delay_in", "delay_out": "seq_delay_out"}
    payload.update({name: float(getattr(clip, field, 0.0) or 0.0)
                    for name, field in fields.items()})
    payload.update(input_direction=input_direction != "none",
                   output_direction=output_direction != "none")
    return clock.table_scalars(payload)
