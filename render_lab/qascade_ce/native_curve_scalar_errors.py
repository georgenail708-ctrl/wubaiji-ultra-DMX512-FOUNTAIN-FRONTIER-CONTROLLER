"""Original semantic stage reconstruction; no second parse or numeric retry."""


def _none_error():
    return TypeError("float() argument must be a string or a real number, not 'NoneType'")


def raise_semantic(report, prepared):
    result = report.result
    recorded = prepared.errors.get(result.selector)
    if recorded is not None:
        error_type, message = recorded
        raise error_type(message)
    if result.semantic == 8:
        raise IndexError("list index out of range")
    if result.semantic == 1:
        raise OverflowError("int too large to convert to float")
    if result.semantic == 4:
        raise ZeroDivisionError("float division by zero")
    if result.semantic == 6:
        raise _none_error()
    raise RuntimeError(f"Unmapped scalar semantic {result.semantic} at stage {result.stage}")


def result_value(report, prepared):
    result = report.result
    if result.kind == 3:
        raise_semantic(report, prepared)
    if result.kind == 1:
        if not 0 <= result.selector < len(prepared.owners):
            raise RuntimeError("Native scalar returned an invalid original-object selector")
        return prepared.owners[result.selector]
    if result.kind == 2:
        return result.number
    raise RuntimeError("Native scalar returned no defined public result")
