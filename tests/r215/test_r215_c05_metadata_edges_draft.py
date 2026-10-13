"""Additional non-class layout and mutable symbol-map qualification controls."""
import pytest

from test_r215_c05_qualification_tamper_draft import source_world, mounted_world


@pytest.mark.parametrize('timing', ['before-first-qualification', 'after-qualification'])
@pytest.mark.parametrize('field', ['_INT_INPUT', '_DOUBLE_INPUT', '_SCALAR_OPS'])
def test_non_layout_objects_veto_without_raising(mounted_world, monkeypatch, timing, field):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    if timing == 'after-qualification':
        assert causal.default_scope(engine) is True
    monkeypatch.setattr(clock, field, object())
    assert causal.default_scope(engine) is False


def test_mutable_map_symbol_change_vetoes_current_scope(mounted_world, monkeypatch):
    from qascade_ce import native_binding
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    assert causal.default_scope(engine) is True
    monkeypatch.setitem(native_binding.CE_API_MAP, 'ce_source_motion_clock', 'unreviewed-symbol')
    assert causal.default_scope(engine) is False
