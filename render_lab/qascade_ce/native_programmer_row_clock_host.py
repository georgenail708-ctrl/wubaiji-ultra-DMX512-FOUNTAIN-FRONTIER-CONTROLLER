"""Original public clock order, with a single actual TICK19 C entry."""
from .native_programmer_row_payload import make
from .native_programmer_row_qualification import plain_clock
from .native_programmer_row_queries import one


def _foreign_tick(host, trajectory, now, read):
    dt = now - host._traj_last_tick
    host._traj_last_tick = now
    host._traj_play_t += dt
    duration = float(trajectory.duration)
    if host._traj_play_t >= duration:
        if trajectory.loop:
            host._traj_play_t = host._traj_play_t % duration if duration > 0 else 0.0
        else:
            host._traj_play_t = duration
            host._traj_emit_output(trajectory, host._traj_play_t)
            host._traj_update_transport_ui()
            host._traj_stop_internal()
            return
    host._traj_emit_output(trajectory, host._traj_play_t)
    read(host)
    host._traj_update_transport_ui()


def tick(context, host, panel):
    trajectory = host._traj_selected()
    if trajectory is None or not host._traj_playing:
        return
    now = panel._time.monotonic()
    if not plain_clock(host, trajectory) or type(now) not in (bool, int, float):
        context.statistics.compatibility("unsupported")
        return _foreign_tick(host, trajectory, now, panel.automation_read_step)
    item = dict(value_offset=0, value_count=4, flags=int(bool(trajectory.loop)))
    values = (now, host._traj_last_tick, host._traj_play_t, trajectory.duration)
    result = context.native.run(make("TICK", values, [item]))
    indices, outputs = result.indices(), result.values()
    commit, route = indices
    if commit & 1:
        host._traj_last_tick = outputs[0]
    if commit & 2:
        host._traj_play_t = outputs[1]
    result.raise_semantic()
    host._traj_emit_output(trajectory, host._traj_play_t)
    if route == 2:
        host._traj_update_transport_ui()
        host._traj_stop_internal()
        return
    panel.automation_read_step(host)
    host._traj_update_transport_ui()


def scrub(context, host, value):
    trajectory = host._traj_selected()
    if trajectory is None:
        return
    if not plain_clock(host, trajectory, False) or type(value) not in (bool, int, float):
        context.statistics.compatibility("unsupported")
        duration = float(trajectory.duration)
        position = max(0.0, min(duration, duration * value / 1000.0))
    else:
        result = one(context.native, "SCRUB", (trajectory.duration, value))
        position, duration = result.numbers()
    host._traj_play_t = position
    host._traj_emit_output(trajectory, position)
    host._traj_time_lbl.setText(f"{position:.2f} / {duration:.2f}s")
