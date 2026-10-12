"""Owned lazy WebEngine adapter to the offline Three renderer (PV-REAL).

Existing Qascade WebChannel/resource/packaging APIs, bounded latest state.
No output writer, Lab service, media, ML runtime, or application event loop.
"""
import json
from pathlib import Path
import sys
from time import monotonic
from uuid import uuid4

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from .output_preview_camera import fixture_pose


def resource_path():
    roots = (getattr(sys, "_MEIPASS", ""), Path(sys.executable).parent,
             Path(__file__).resolve().parents[2])
    for root in roots:
        path = Path(root) / "assets/output_preview/index.html"
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("离线 3D 灯水资源未随包分发")


class _Bridge(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    @pyqtSlot()
    def ready(self):
        self.owner._ready = True
        self.owner.sync()

    @pyqtSlot(str, int)
    def ack(self, token, sequence):
        self.owner.ack(token, sequence)

    @pyqtSlot(str, str, bool, bool)
    def selection(self, token, encoded, additive, single):
        if token != self.owner.token or not self.owner._active:
            return
        ids = json.loads(encoded)
        valid = {x.fixture.fixture_id for x in self.owner.data.outputs}
        ids = [x for x in ids if isinstance(x, str) and x in valid]
        if single and ids:
            self.owner.picked.emit(ids[0], additive)
        elif not single:
            self.owner.box_selected.emit(ids, additive)

    @pyqtSlot(str)
    def failed(self, message):
        self.owner._ready = False
        self.owner.failure.emit(message)


class OutputPreviewWeb(QWidget):
    picked = pyqtSignal(str, bool)
    box_selected = pyqtSignal(list, bool)
    failure = pyqtSignal(str)

    def __init__(self, data, parent=None):
        super().__init__(parent)
        # Import only when an operator selects 3D; no second QApplication.
        from PyQt6.QtWebChannel import QWebChannel
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        self.data = data
        self.token = ""
        self._session = uuid4().hex
        self._ready = self._active = self._disposed = False
        self._configuration = None
        self._signatures = {}
        self._selected = []
        self._inflight = None
        self._sequence = 0
        self._generation = 0
        self._pending = False
        self._sent_at = 0
        self.view = QWebEngineView(self)
        # QObject destroys children in insertion order: view/page before profile.
        self.profile = QWebEngineProfile(self)
        self.page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.page)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.bridge = _Bridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("previewBridge", self.bridge)
        self.page.setWebChannel(self.channel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.loadFinished.connect(self._loaded)
        self.page.renderProcessTerminated.connect(self._terminated)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.dispose)
        self.view.load(QUrl.fromLocalFile(str(resource_path())))

    def _loaded(self, success):
        if not success and not self._disposed:
            self.failure.emit("离线 3D 页面加载失败")

    def _terminated(self, status, code):
        if not self._disposed:
            self._ready = False
            self.failure.emit(f"3D 图形进程结束: {status.name} / {code}")

    def _js(self, method, argument=None):
        text = "" if argument is None else json.dumps(argument, ensure_ascii=False, allow_nan=False)
        self.page.runJavaScript(f"window.preview && window.preview.{method}({text});")

    def configure(self):
        fixtures = []
        for output in self.data.outputs:
            f, p = output.fixture, output.fixture.profile
            if f.patch_error:
                continue
            fixtures.append({"id": f.fixture_id, "universe": f.universe, "name": f.name,
                             "anchor": p["anchor"], "beam": p["beam"], "water": p["water"]})
        self._generation += 1
        self.token = f"{self._session}:{self._generation}:{self.data.epoch}"
        self._inflight = None
        self._signatures.clear()
        self._configuration = (id(self.data.outputs), self.data.epoch)
        self._js("configure", {"token": self.token, "epoch": self.data.epoch,
                               "fixtures": fixtures, "selected": self._selected})
        if self._active:
            self._js("resume")

    def _changed_states(self):
        """Collect changed fixture values while retaining the same signature cache."""
        states = []
        for output in self.data.outputs:
            if output.fixture.patch_error:
                continue
            signature = (output.values, output.observed)
            if self._signatures.get(output.fixture.fixture_id) == signature:
                continue
            anchor, direction = fixture_pose(output)
            states.append({"id": output.fixture.fixture_id, "anchor": anchor, "direction": direction,
                           "rgb": output.rgb, "light": output.light if output.observed else 0,
                           "water": output.water if output.observed else 0, "zoom": output.zoom})
            self._signatures[output.fixture.fixture_id] = signature
        return states

    def sync(self):
        if not self._active or not self._ready or self._disposed:
            return
        if self._configuration != (id(self.data.outputs), self.data.epoch):
            self.configure()
        if self._inflight is not None:
            self._pending = True
            if monotonic() - self._sent_at > 3:
                self._ready = False
                self.failure.emit("3D 显示响应超时，输出业务继续")
            return
        states = self._changed_states()
        now = monotonic()
        fresh = [(universe, max(0, 2 - (now - frame.attempted_at_monotonic)))
                 for universe, frame in self.data.frames.items()]
        self._sequence += 1
        self._inflight, self._sent_at = self._sequence, now
        self._pending = False
        self._js("apply", {"token": self.token, "epoch": self.data.epoch,
                           "sequence": self._sequence, "revision": self.data.revision,
                           "states": states, "fresh": fresh})

    def ack(self, token, sequence):
        if token != self.token or sequence != self._inflight or self._disposed:
            return
        self._inflight = None
        if self._pending:
            self.sync()

    def set_selection(self, ids):
        self._selected = list(ids)
        if self._ready and not self._disposed:
            self._js("setSelection", self._selected)

    def set_active(self, active):
        self._active = active and not self._disposed
        if self._ready and not self._disposed:
            if active:
                self._js("resume")
                self.sync()
            else:
                self._js("pause")

    def fit(self, selected=False):
        if self._ready and not self._disposed:
            self._js("fit", bool(selected))

    @pyqtSlot()
    def dispose(self):
        if self._disposed:
            return
        self._active = False
        if self._ready:
            self._js("dispose")
        self._disposed = True
        self._inflight = None
        self.token = ""
        self.page.setWebChannel(None)
        self.view.stop()

    def hideEvent(self, event):
        self.set_active(False)
        super().hideEvent(event)

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)
