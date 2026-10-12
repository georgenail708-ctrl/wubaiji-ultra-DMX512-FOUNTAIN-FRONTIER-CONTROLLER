"""R36 compact main control; reuse PanelToolbar, Qt button and existing gate.

Reference: Qt6.11 qtoolbutton.cpp (standard text/icon API), Tabler MIT.
The program owns persistence; signal-blocked view refresh never edits it.
"""
from PyQt6.QtCore import QEvent, QObject, QSignalBlocker, Qt
from PyQt6.QtWidgets import QToolButton

from ..core.program_lighting import active_program, has_program_light_state
from ..core.project_manager import DOMAIN_TIMELINE
from ..engine.clip_output_filters import set_light_suppressed
from .main_icon_library import load_tabler_icon


def program_light_managed(project):
    """Shared ownership check for legacy Studio writers and load reset."""
    return has_program_light_state(project)


def _legacy_enabled(project, program):
    """Migrate only applied sheets bound to this program, preserving other cards."""
    from .clip_studio.studio_compile import LIGHT_SUPPRESS_KEY, compile_sheet
    tracks = project.tracks if str(program.id) == str(project.current_program_id) else program.tracks
    bound = {str(c.process_sheet_id) for t in tracks for c in t.clips if c.process_sheet_id}
    sheets = (getattr(project, "clip_studio_state", {}) or {}).get("sheets", [])
    for sheet in sheets:
        if str(sheet.get("id", "")) not in bound:
            continue
        compiled = compile_sheet(sheet)
        if not compiled.errors and compiled.fields.get(LIGHT_SUPPRESS_KEY, False):
            return False
    return True


def migrate_program_lights(project):
    """Missing legacy keys get their program's original bound-card contribution once."""
    for program in getattr(project, "programs", []):
        if getattr(program, "light_enabled", None) is None:
            program.light_enabled = _legacy_enabled(project, program)


class ProgramLightControl(QObject):
    """One owned button, no timer, output layer or competing settings store."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.button = QToolButton(window)
        self.button.setObjectName("program_light_control")
        self.button.setCheckable(True)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setAccessibleName("当前节目灯光开关")
        self.button.setFixedHeight(24)
        self.button.setStyleSheet("QToolButton { min-height:18px; max-height:18px; padding:2px 6px; }")
        self.button.setMinimumWidth(72)
        self.button.installEventFilter(self)
        self.button.clicked.connect(self.set_enabled)
        window._panel_toolbar.add_tail_widget(self.button)
        self.refresh()

    def eventFilter(self, watched, event):
        """Focused Space belongs to this button rather than Main's play action."""
        if watched is self.button and event.type() == QEvent.Type.ShortcutOverride:
            if event.key() == Qt.Key.Key_Space:
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def refresh(self):
        """Switch/load/undo only derives the gate and shows the active program value."""
        project = self.window._project()
        migrate_program_lights(project)
        program = active_program(project)
        enabled = True if program is None else bool(program.light_enabled)
        set_light_suppressed(not enabled)
        with QSignalBlocker(self.button):
            self.button.setChecked(enabled)
        self.button.setEnabled(program is not None)
        self.button.setText("灯光开" if enabled else "灯光关")
        self.button.setIcon(load_tabler_icon("bulb" if enabled else "bulb-off", 16))
        self.button.setToolTip("当前节目灯光总开关；开启恢复原灯光输出，关闭仅屏蔽灯光。随节目保存。")

    def set_enabled(self, enabled):
        """One user edit, one existing timeline-domain transaction."""
        project = self.window._project()
        program = active_program(project)
        if program is None or program.light_enabled == bool(enabled):
            self.refresh()
            return
        self.window._pm._snapshot_scoped(domain=DOMAIN_TIMELINE)
        program.light_enabled = bool(enabled)
        self.refresh()
        self.window.mark_dirty()


def install_program_light_control(window):
    """Thin MainWindow seam, idempotent and owned by the original window."""
    if getattr(window, "_program_light_control", None) is None:
        window._program_light_control = ProgramLightControl(window)


def refresh_program_light_control(window):
    """Original program-list/restore path; safe before the toolbar exists."""
    window._apply_timeline_program_gate()
    control = getattr(window, "_program_light_control", None)
    if control is not None:
        control.refresh()
