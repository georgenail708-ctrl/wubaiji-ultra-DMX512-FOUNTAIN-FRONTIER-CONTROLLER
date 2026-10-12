"""Decode only the actual first failure descriptor; no future parser scan."""
SEMANTIC = {
    1: (OverflowError, "int too large to convert to float"),
    2: (ValueError, "cannot convert float NaN to integer"),
    3: (OverflowError, "cannot convert float infinity to integer"),
    4: (ZeroDivisionError, "float division by zero"),
    9: (ValueError, "cannot convert float NaN to integer"),
    10: (OverflowError, "cannot convert float infinity to integer"),
    11: (OverflowError, "integer division result too large for a float"),
}


def semantic_error(report, prepared):
    selector = int(report.reserved) - 1
    if selector >= len(prepared.owners):
        raise RuntimeError("Row failure descriptor is outside the original owner table")
    if report.semantic in (1, 6, 7) and selector in prepared.errors:
        return prepared.errors[selector]
    kind, message = SEMANTIC.get(report.semantic, (RuntimeError, "unmapped row semantic error"))
    return kind(message)
