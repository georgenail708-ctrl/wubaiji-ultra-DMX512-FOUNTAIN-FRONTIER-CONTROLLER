"""Canonical Programmer work counters and transactional default installation."""
from .native_programmer_trajectory_statistics import Statistics

_STATISTICS = Statistics()
_STATISTICS.publish(globals())


def counters():
    return _STATISTICS.counters()


def available():
    from .native_programmer_owner import binding_snapshot
    return binding_snapshot() is not None


def install(engine):
    from .native_programmer_owner import install as install_domains
    return install_domains(engine)


def row_semantic_error(report, prepared):
    from .native_programmer_row_result import semantic_error
    return semantic_error(report, prepared)
