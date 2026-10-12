"""Original paired spin feedback with truthful Qt endpoint constraints."""


def restore_pair_values(pair, values):
    """Restore both endpoints together so the old peer cannot clamp either one."""
    complete = {**pair.get_values(), **values}
    pair._set_pair_values(complete[pair._low_spec.key], complete[pair._high_spec.key])


def _absolute_bounds(pair, spin, spec):
    integer = str(spec.type).lower() == "int"
    cast = int if integer else float
    low = cast(pair._number_attr(spec, "min_val", 0))
    high = max(low, cast(pair._number_attr(spec, "max_val", 255 if integer else 1)))
    spin.setRange(low, high)


def _sync_value(spin, value):
    quantized = spin.valueFromText(spin.textFromValue(value))
    if spin.value() != quantized:
        spin.setValue(value)


def sync_pair_spins(pair, low, high):
    low = pair._cast_value(pair._low_spec, low)
    high = pair._cast_value(pair._high_spec, high)
    low_spin, high_spin = pair._low_spin, pair._high_spin
    low_blocked = low_spin.blockSignals(True)
    high_blocked = high_spin.blockSignals(True)
    try:
        # Allow canonical programmatic pairs to move both endpoints beyond the
        # previous pair. No intermediate range is emitted to the application.
        _absolute_bounds(pair, low_spin, pair._low_spec)
        _absolute_bounds(pair, high_spin, pair._high_spec)
        _sync_value(low_spin, low)
        _sync_value(high_spin, high)
        low, high = low_spin.value(), high_spin.value()
        low_spin.setMaximum(min(low_spin.maximum(), high))
        high_spin.setMinimum(max(high_spin.minimum(), low))
        return low, high
    finally:
        low_spin.blockSignals(low_blocked)
        high_spin.blockSignals(high_blocked)
