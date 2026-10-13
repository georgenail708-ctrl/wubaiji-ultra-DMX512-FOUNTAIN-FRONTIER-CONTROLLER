"""Independent default install/current and original4 public C paths; ROOT only."""
import importlib
import importlib.util
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
OLD = HERE / 'test_audio_preparation_draft.py'
spec = importlib.util.spec_from_file_location('_r215_c06_default_business_review', OLD)
business = importlib.util.module_from_spec(spec)
spec.loader.exec_module(business)
world = business.world


def parts():
    return tuple(importlib.import_module('qascade_ce.' + name) for name in (
        'native_audio_prepare', 'native_audio_prepare_owner', 'native_audio_prepare_scope',
        'native_audio_prepare_dependencies'))


def test_actual_default_owner_installs_and_preserves_qualified_change_constructor_key(world):
    api, owner, scope, dependencies = parts()
    import qascade_ce
    assert owner._OWNER is not None, 'Default original environment rejected C06 owner'
    actual = owner._OWNER
    assert actual.binding.engine is qascade_ce._engine
    assert actual.guard.safe is True and scope.current(actual) is True
    rows = dependencies.supplier_functions()
    assert rows is not None
    row = next(row for row in rows if row[0].__name__ == 'qascade_ce.native_programmer_transaction'
               and row[1].__name__ == 'Change')
    assert row[2] == 'Change.__init__'
    assert dependencies.preflight() is True
    assert owner.install(qascade_ce._engine) is True, 'Default same-engine reinstall should preserve owned installation'
    assert owner._OWNER is actual and scope.current(actual) is True
    for module, name, wrapper in owner._alias_rows(actual.wrappers):
        assert vars(module)[name] is wrapper
    assert api.available() is True and api.counters()['pending'] == 0


@pytest.mark.parametrize('selection,raw,operation,work', [
    (('audio_mod_processor_schema', 'normalize_processors'), [{'kind': 'accent'}], 'PROCESSORS', 'processor_values'),
    (('audio_modulation', 'normalize_state'), {}, 'STATE', 'state_values'),
    (('audio_mod_envelope', 'normalize_points'), [[0., 0.], [.5, .7, .1], [1., 1.]], 'POINTS', 'point_rows'),
    (('audio_mod_spread', 'clamp_deg'), 45., 'STATE', 'state_values')])
def test_default_public_role_original_full_bits_then_one_actual_native_batch_and_current_owner(
        world, selection, raw, operation, work):
    api, owner, scope, _dependencies = parts()
    assert owner._OWNER is not None and scope.current(owner._OWNER) is True
    current = owner._OWNER
    before = api.counters()
    business.numerical_case(world, selection, raw, operation, work)
    after = api.counters()
    assert after['calls'] == before['calls'] + 1, 'Default public normalization must consume one batch'
    assert owner._OWNER is current and scope.current(current) is True
