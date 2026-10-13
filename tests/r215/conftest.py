"""Public synthetic CE test setup with real owned shutdown and temp settings."""
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR','1')

ROOT = next(path for path in Path(__file__).resolve().parents
    if (path/'src/core/models.py').is_file())
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'render_lab'))

import pytest
from PyQt6 import QtCore
from PyQt6.QtWidgets import QApplication

_RealQSettings = QtCore.QSettings
_settings_owner = None
_settings_paths = {}
_app_owner = None


def _ini_settings(*args,**kwargs):
    if len(args) == 2 and all(isinstance(value,str) for value in args):
        return _RealQSettings(_RealQSettings.Format.IniFormat,
            _RealQSettings.Scope.UserScope,*args,**kwargs)
    return _RealQSettings(*args,**kwargs)


def pytest_configure(config):
    global _settings_owner
    _settings_owner = tempfile.TemporaryDirectory(prefix='qascade_public_test_',ignore_cleanup_errors=True)
    for scope in (_RealQSettings.Scope.UserScope,_RealQSettings.Scope.SystemScope):
        probe = _RealQSettings(_RealQSettings.Format.IniFormat,scope,'qascade-public-path','probe')
        _settings_paths[scope] = str(Path(probe.fileName()).parent.parent)
        _RealQSettings.setPath(_RealQSettings.Format.IniFormat,scope,_settings_owner.name)
    QtCore.QSettings = _ini_settings


def _stop_loaded_workers():
    # Calling product shutdown does not import/start a previously unused worker.
    for module_name,name in (('src.ui.audio_driver','_stop_global_analyzer_at_exit'),
            ('src.engine.timeline_autobake','_shutdown_all_bakers')):
        module = sys.modules.get(module_name)
        if module is not None:
            getattr(module,name)()


def pytest_sessionfinish(session,exitstatus):
    _stop_loaded_workers()


def _restore_settings():
    QtCore.QSettings = _RealQSettings
    for scope,path in _settings_paths.items():
        _RealQSettings.setPath(_RealQSettings.Format.IniFormat,scope,path)
    for name,module in list(sys.modules.items()):
        if name.startswith('src.') and module is not None:
            namespace = vars(module)
            if namespace.get('QSettings') is _ini_settings:
                namespace['QSettings'] = _RealQSettings


def pytest_unconfigure(config):
    global _app_owner
    _stop_loaded_workers()
    if _app_owner is not None:
        _app_owner.processEvents()
    _restore_settings()
    if _settings_owner is not None:
        _settings_owner.cleanup()
    _app_owner = None


@pytest.fixture(scope='session',autouse=True)
def _session_qapp():
    global _app_owner
    app = QApplication.instance() or QApplication(['qascade-public-ce-tests'])
    _app_owner = app
    yield app
    _stop_loaded_workers()
    app.processEvents()


@pytest.fixture(scope='session')
def qapp(_session_qapp):
    return _session_qapp
