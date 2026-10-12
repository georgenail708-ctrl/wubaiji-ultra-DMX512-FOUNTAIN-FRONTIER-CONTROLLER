"""Serial, immutable save requests owned by the original document window.

Reuse ProjectSaver and Project's atomic writer. A completed old snapshot must
never certify newer edits or rebind a different document.
"""
import pickle
from dataclasses import dataclass
from pathlib import Path
from functools import wraps

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget


def edit_stamp(win):
    return (getattr(win, '_save_edit_revision', 0),
            getattr(win._pm, '_edit_revision', 0))


def note_project_edit(win):
    win._dirty = True
    win._save_edit_revision = getattr(win, '_save_edit_revision', 0) + 1


def freeze_save_payload(payload):
    """Copy our freshly built in-memory payload through the stdlib C codec.

    No pickle bytes are read from files or external sources; showfiles remain
    original JSON. This preserves key/tuple/float semantics and breaks aliases.
    """
    return pickle.loads(pickle.dumps(payload, protocol=5))


@dataclass
class SaveRequest:
    payload: dict
    path: str
    project: object
    stamp: tuple


class SaveSession(QObject):
    idle = pyqtSignal()

    def __init__(self, win):
        super().__init__(win if isinstance(win, QObject) else None)
        self.win = win
        self.active = None
        self.pending = []
        self.error = ''

    def enqueue(self, path):
        project = self.win._pm.project
        request = SaveRequest(freeze_save_payload(project.to_dict()), str(path), project,
                              edit_stamp(self.win))
        # Repeated Ctrl+S coalesces one waiting snapshot per document and path.
        self.pending = [old for old in self.pending
                        if not (old.project is project and old.path == request.path)]
        self.pending.append(request)
        self.start_next()

    def start_next(self):
        # ★ R193/P2-09（SV-05）：文档换代后丢弃旧文档的排队快照——写盘不可撤，
        # 但还没开始的可以不写（在途的那一写由 identity 校验兜住收尾语义）。
        self.pending = [old for old in self.pending
                        if old.project is self.win._pm.project]
        if self.active is not None or not self.pending:
            return
        from .project_save_async import ProjectSaver
        request = self.pending.pop(0)
        parent = self.win if isinstance(self.win, QObject) else None
        worker = ProjectSaver(request.payload, request.path, parent=parent)
        self.active = (request, worker)
        self.win._save_thread = worker
        self.error = ''
        worker.failed.connect(self.on_failed)
        worker.saved.connect(self.on_saved)
        worker.finished.connect(self.on_finished)
        self.win._status.set_message(f'正在保存… {request.path}')
        worker.start()

    def on_failed(self, message):
        self.error = str(message)

    def on_saved(self, ok):
        request, _worker = self.active
        if not ok:
            self.report_failure(request)
            return
        if self.win._pm.project is not request.project:
            return
        self.win._project_path = request.path
        self.win._pm._current_path = request.path
        if edit_stamp(self.win) == request.stamp:
            self.win._pm._dirty = False
            self.win._dirty = False
            signal = getattr(self.win._pm, 'dirty_changed', None)
            if signal is not None:
                signal.emit(False)
        self.win._update_title()
        self.win._status.set_message(f'已保存: {request.path}')
        if isinstance(self.win, QWidget):
            from .toast_notification import show_success_toast
            show_success_toast(self.win, '项目已保存', f'文件: {Path(request.path).name}')

    def on_finished(self):
        _request, worker = self.active
        worker._payload = None
        worker.deleteLater()
        self.win._save_thread = None
        self.active = None
        self.start_next()
        if self.active is None:
            self.idle.emit()

    def report_failure(self, request):
        parent = self.win if isinstance(self.win, QObject) else None
        message = self.error or '请检查路径权限或磁盘空间。'
        QMessageBox.critical(parent, '保存失败', f'无法写入文件：{request.path}\n{message}')


def save_session(win):
    session = getattr(win, '_project_save_session', None)
    if session is None:
        session = SaveSession(win)
        win._project_save_session = session
    return session


def close_after_project_saves(method):
    """Defer the user's close until our writer queue is finished, keeping UI live."""
    @wraps(method)
    def close(win, event):
        session = getattr(win, '_project_save_session', None)
        if session is None or session.active is None:
            return method(win, event)
        event.ignore()
        win._status.set_message('正在保存，完成后关闭…')
        if not getattr(session, '_close_requested', False):
            session._close_requested = True
            def resume():
                session.idle.disconnect(resume)
                session._close_requested = False
                win.close()
            session.idle.connect(resume)
        return None
    return close
