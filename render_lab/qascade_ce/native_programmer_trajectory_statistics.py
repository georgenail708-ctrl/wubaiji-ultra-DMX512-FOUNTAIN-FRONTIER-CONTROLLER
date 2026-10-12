"""Root-owned counter lists; supplied only by actual typed C-return reports."""

PREFIX = "_PROGRAMMER_TRAJECTORY_"
BASE = ("CALL", "SUCCESS", "RETURN_OK", "ERROR", "FALLBACK", "UNSUPPORTED", "OVERRIDE",
        "SEMANTIC_ERROR", "READ", "ROW", "RANGE", "DEFAULT", "PHYSICAL", "FILL", "CLOCK",
        "CURVE", "MEAN", "INTEGER_ARITHMETIC", "INTEGER_CONVERSIONS", "ROW_REPORTED_SEMANTIC_ERRORS")
LIBRARY = ("values", "quantize", "segment", "direction", "reveal", "shape", "window",
           "morph", "blend", "partition", "offsets")
SCALAR = ("phase", "model", "segment", "bezier", "bezier_samples", "values", "clamps",
          "integer_subtract", "integer_conversions", "semantic_errors", "handled_semantic")
ROW = dict(read="READ", range="RANGE", default_value="DEFAULT", physical="PHYSICAL",
           fill="FILL", clock="CLOCK", curve="CURVE", mean="MEAN",
           integer_arithmetic="INTEGER_ARITHMETIC", integer_conversions="INTEGER_CONVERSIONS",
           semantic_errors="ROW_REPORTED_SEMANTIC_ERRORS")


def _names():
    domains = ("LIBRARY", "ROW")
    names = set(BASE) | {name.upper() for name in LIBRARY}
    names.update("SCALAR_" + name.upper() for name in SCALAR)
    names.update(domain + "_" + suffix for domain in domains
                 for suffix in ("CALL", "SUCCESS", "RETURN_OK", "ERROR", "SEMANTIC_ERROR"))
    names.update("LIBRARY_" + name.upper() for name in LIBRARY)
    names.update("ROW_" + name.upper() for name in ("read", "row", *ROW))
    names.update("SORT_" + name for name in ("CALL", "SUCCESS", "ERROR", "KEYS", "COMPARE", "RUNS", "MERGES"))
    return sorted(names)


class Statistics:
    """The single canonical owner creates and retains this one instance."""

    def __init__(self):
        self.owners = {name: [0] for name in _names()}

    def add(self, name, value=1):
        self.owners[name][0] += value

    def publish(self, namespace):
        for name, owner in self.owners.items():
            namespace[f"{PREFIX}{name}_CNT"] = owner

    def counters(self):
        return {f"{PREFIX}{name}_CNT": owner[0] for name, owner in self.owners.items()}

    def returned(self, domain, code, report):
        self.add("CALL")
        self.add(domain + "_CALL")
        if code != 0 or report.status != code:
            self.add("ERROR")
            self.add(domain + "_ERROR")
            return
        self.add("RETURN_OK")
        self.add(domain + "_RETURN_OK")
        if report.semantic:
            self.add("SEMANTIC_ERROR")
            self.add(domain + "_SEMANTIC_ERROR")
            return
        self.add("SUCCESS")
        self.add(domain + "_SUCCESS")

    def record_library(self, code, report):
        self.returned("LIBRARY", code, report)
        for field in LIBRARY:
            work = getattr(report, field)
            self.add(field.upper(), work)
            self.add("LIBRARY_" + field.upper(), work)
        for field in SCALAR:
            self.add("SCALAR_" + field.upper(), getattr(report.scalar, field))
        self.fold_scalar_sort(report.scalar_sort)

    def fold_scalar_sort(self, report):
        self.add("SORT_CALL", report.calls)
        for field, name in (("keys", "KEYS"), ("comparisons", "COMPARE"),
                            ("runs", "RUNS"), ("merges", "MERGES")):
            self.add("SORT_" + name, getattr(report, field))

    def record_row(self, operation, code, report):
        # operation names describe the actual API request; never supply work.
        self.returned("ROW", code, report)
        self.add("ROW", report.row + report.range + report.mean + report.curve)
        self.add("ROW_ROW", report.row)
        for field, name in ROW.items():
            work = getattr(report, field)
            self.add(name, work)
            self.add("ROW_" + field.upper(), work)

    def record_sort(self, code, report):
        self.returned("LIBRARY", code, report)
        self.add("SORT_CALL")
        if code or report.status != code:
            self.add("SORT_ERROR")
        elif not report.semantic:
            self.add("SORT_SUCCESS")
        for field, name in (("keys", "KEYS"), ("comparisons", "COMPARE"),
                            ("runs", "RUNS"), ("merges", "MERGES")):
            self.add("SORT_" + name, getattr(report, field))
        self.add("DIRECTION", report.keys)
        self.add("LIBRARY_DIRECTION", report.keys)
        self.add("VALUES", report.values)
        self.add("LIBRARY_VALUES", report.values)
        for field in SCALAR:
            self.add("SCALAR_" + field.upper(), getattr(report.scalar, field))

    def compatibility(self, kind):
        name = {"unsupported": "UNSUPPORTED", "fallback": "FALLBACK",
                "override": "OVERRIDE"}[kind]
        self.add(name)


class LibraryObserver:
    def __init__(self, statistics):
        self.statistics = statistics

    def record(self, code, report):
        self.statistics.record_library(code, report)

    def compat(self, kind):
        self.statistics.compatibility(kind)

    def record_sort(self, code, report):
        self.statistics.record_sort(code, report)
