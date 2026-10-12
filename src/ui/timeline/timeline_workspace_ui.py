"""Timeline editor assembly using the existing QWidget and domain objects.

References: 编辑器分区; Show Sequencer; Qt QSplitter (Qt API,
no upstream source copied); bundled Tabler icons (MIT). See the R185 Timeline
adoption report for pinned sources, licensing and the original interaction
contracts. No transport, selection model, timer or timeline algorithm here.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QSignalBlocker, QSize, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget,
)

from ...core.clip_postprocess import describe_groups
from .. import qascade_theme as theme
from ..widget_kit import PanelSection, scope_qss
from ..main_icon_library import load_tabler_icon, make_main_icon
from .audio_mod_track import AudioModTrack
from .audio_ref_track import AudioRefTrack
from .clip_area import ClipArea
from .clip_context_menu import _studio_sheet_name
from .timeline_ruler import TimelineRuler
from .timeline_zoom import ZOOM_PRESETS, px_per_sec_for_span
from .track_list import TrackList


def _button_style(*, active=False) -> str:
    background = theme.SELECT_BLUE if active else theme.BG_BUTTON
    foreground = theme.TEXT_SELECTED_FG if active else theme.TEXT_TERTIARY
    return (
        f"QPushButton{{background:{background};color:{foreground};"
        f"border:1px solid {theme.BORDER_NORMAL};border-radius:{theme.RADIUS}px;"
        f"font-size:{theme.FS_SMALL}px;min-height:0px;padding:0px;}}"
        f"QPushButton:hover{{background:{theme.BG_BUTTON_HOVER};}}"
        f"QPushButton:focus{{border-color:{theme.FOCUS_BLUE};}}"
    )


def _glyph_button(parent, *, glyph, tooltip, callback, color=None):
    button = QPushButton(parent)
    button.setFixedSize(24, 24)
    button.setIcon(load_tabler_icon(glyph, 16, color or theme.TEXT_TERTIARY))
    button.setIconSize(QSize(16, 16))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setStyleSheet(_button_style())
    button.clicked.connect(callback)
    return button


def _build_tools(panel, parent):
    bar = QWidget(parent)
    bar.setFixedSize(72, 32)
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    panel._tool_btn_ss_active = _button_style(active=True)
    panel._tool_btn_ss_idle = _button_style()
    for key, glyph, tooltip in (
        ("select", "select", "选择工具 (V)"),
        ("razor", "razor", "剥刀工具 (B) — 切割 Clip"),
    ):
        button = QPushButton(bar)
        button.setIcon(make_main_icon(glyph, 16))
        button.setFixedSize(28, 24)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setStyleSheet(panel._tool_btn_ss_active if key == "select"
                            else panel._tool_btn_ss_idle)
        button.clicked.connect(lambda checked=False, tool=key: panel.set_tool(tool))
        setattr(panel, f"_btn_tool_{key}", button)
        layout.addWidget(button)
    # Existing PEN/Snap extensions append to this same parent/layout (+31 each).
    return bar


def _build_header(panel):
    header = QWidget(panel)
    scope_qss(header, f"background:{theme.BG_ROOT};border-bottom:1px solid {theme.SEP_LINE};")
    layout = QHBoxLayout(header)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(8)
    title = QLabel("时间线编排", header)
    title.setStyleSheet(f"font-weight:bold;color:{theme.TEXT_PRIMARY};")
    layout.addWidget(title)
    layout.addWidget(_build_tools(panel, header))
    layout.addStretch()
    view = QPushButton("视图", header)
    view.setIcon(load_tabler_icon("zoom-in", 16))
    view.setFixedHeight(28)
    view.setToolTip("时间轴缩放；保持播放指针的屏幕位置")
    menu = QMenu(view)
    panel._workspace_zoom_actions = []
    for label, seconds in ZOOM_PRESETS:
        action = menu.addAction(label)
        action.setData(seconds)
        panel._workspace_zoom_actions.append(action)
    view.setMenu(menu)
    layout.addWidget(view)
    toggle = QPushButton("上下文", header)
    toggle.setIcon(load_tabler_icon("layout-sidebar-right", 16))
    toggle.setCheckable(True)
    toggle.setChecked(True)
    toggle.setFixedHeight(28)
    toggle.setToolTip("显示对象读数；窄窗收起后仍可手动打开")
    toggle.setAccessibleName("显示时间线对象上下文")
    layout.addWidget(toggle)
    panel._workspace_context_toggle = toggle
    return header


def _build_track_header(panel, parent):
    header = QWidget(parent)
    header.setFixedSize(AudioRefTrack.CTRL_WIDTH, 32)
    scope_qss(header, f"background:{theme.BG_ROOT};border-bottom:1px solid {theme.SEP_LINE};")
    layout = QHBoxLayout(header)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(0)
    layout.addWidget(QLabel("轨道", header))
    layout.addStretch()
    panel._btn_add_track = _glyph_button(
        header, glyph="circle-plus", tooltip="新增普通轨道",
        callback=panel._on_add_track, color=theme.BRIGHT_SUCCESS)
    panel._btn_add_folder = _glyph_button(
        header, glyph="folder-plus", tooltip="新增轨道文件夹",
        callback=panel._on_add_folder, color=theme.KEYFRAME_GOLD)
    layout.addWidget(panel._btn_add_track)
    layout.addSpacing(8)
    layout.addWidget(panel._btn_add_folder)
    # Existing audio-mod addition appends to this exact QHBoxLayout.
    return header


def _build_editor(panel):
    editor = QWidget(panel)
    editor.setMinimumWidth(320)
    editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    layout = QVBoxLayout(editor)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    ruler_row = QHBoxLayout()
    ruler_row.setContentsMargins(0, 0, 0, 0)
    ruler_row.setSpacing(0)
    panel._track_list_header = _build_track_header(panel, editor)
    ruler_row.addWidget(panel._track_list_header)
    panel._ruler = TimelineRuler(panel.ctrl.px_per_sec, parent=panel)
    ruler_row.addWidget(panel._ruler, 1)
    layout.addLayout(ruler_row)
    content = QHBoxLayout()
    content.setContentsMargins(0, 0, 0, 0)
    content.setSpacing(0)
    panel._track_list = TrackList(parent=panel)
    panel._track_list.setFixedWidth(AudioRefTrack.CTRL_WIDTH)
    content.addWidget(panel._track_list)
    panel._clip_area = ClipArea(panel.ctrl.px_per_sec, parent=panel)
    content.addWidget(panel._clip_area, 1)
    layout.addLayout(content, 1)
    panel._audio_track = AudioRefTrack(parent=panel)
    layout.addWidget(panel._audio_track)
    # State owner stays a hidden child of TimelineWidget, outside every layout.
    panel._audio_mod_track = AudioModTrack(parent=panel)
    return editor


def _readout(section, label):
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    caption = QLabel(label, row)
    caption.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:{theme.FS_SMALL}px;")
    value = QLabel("—", row)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout.addWidget(caption)
    layout.addWidget(value)
    section.add_widget(row)
    return value


def _build_context(panel):
    scroll = QScrollArea(panel)
    scroll.setWidgetResizable(True)
    scroll.setMinimumWidth(220)
    scroll.setMaximumWidth(360)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    scope_qss(body, f"background:{theme.BG_EDITOR};")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)
    note = QLabel("检查对象；批量选择与操作以画布为准。", body)
    note.setWordWrap(True)
    note.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:{theme.FS_SMALL}px;")
    layout.addWidget(note)
    track = PanelSection("轨道上下文", parent=body, icon_key="list-tree")
    layout.addWidget(track)
    fields = {key: _readout(track, label) for key, label in (
        ("track", "所属轨道 / ID"), ("hierarchy", "类型 / 父文件夹"),
        ("filter", "轨道通道过滤"), ("flags", "本轨 M / S"),
    )}
    clip = PanelSection("所查片段", parent=body, icon_key="layout-list")
    layout.addWidget(clip)
    fields.update({key: _readout(clip, label) for key, label in (
        ("clip", "片段 / ID"), ("timing", "起点 / 长度（秒）"),
        ("fade", "淡入 / 淡出（秒）"), ("universes", "Clip 域"),
        ("sheet", "Studio Sheet"), ("processing", "非默认后处理"),
    )})
    studio = QPushButton("Clip 附加处理…", body)
    studio.setIcon(load_tabler_icon("components", 16))
    studio.setToolTip("在 Studio 中打开所查片段；保留原单片段入口")
    clip.add_widget(studio)
    layout.addStretch()
    scroll.setWidget(body)
    return scroll, fields, studio


def _track_values(track, session):
    if track is None:
        return dict.fromkeys(("track", "hierarchy", "filter", "flags"), "—")
    parent = session.get_track(track.parent_id) if track.parent_id else None
    parent_name = parent.name if parent is not None else track.parent_id or "顶层"
    return {
        "track": f"{track.name}\n{track.id}",
        "hierarchy": f"{_track_kind(track)} / {parent_name}",
        "filter": track.filter_label(),
        "flags": f"M：{'开' if track.muted else '关'} / S：{'开' if track.solo else '关'}",
    }


def _track_kind(track):
    if track.is_folder:
        return "文件夹"
    return "音频调制" if track.is_audio_mod else "普通轨道"


def _sheet_text(clip):
    name = _studio_sheet_name(clip)
    if name:
        return name
    sheet_id = str(clip.process_sheet_id or "")
    return f"未解析绑定：{sheet_id}" if sheet_id else "未绑定"


def _processing_text(clip):
    return "\n".join(f"{group}\n" + "\n".join(
        f"{label}：{value}" for label, value in rows)
        for group, rows in describe_groups(clip))


def _clip_values(clip):
    keys = ("clip", "timing", "fade", "universes", "sheet", "processing")
    if clip is None:
        values = dict.fromkeys(keys, "—")
        values["clip"] = "点击片段检查；右击画布保留完整工具菜单。"
        return values
    return {
        "clip": f"{clip.label or clip.preset_name or '未命名片段'}\n{clip.id}",
        "timing": f"{clip.start_time:.3f} / {clip.duration:.3f}",
        "fade": f"{clip.fade_in:.3f} / {clip.fade_out:.3f}",
        "universes": ", ".join(str(value) for value in clip.clip_universes) or "—",
        "sheet": _sheet_text(clip),
        "processing": _processing_text(clip),
    }


class _WorkspaceContext(QObject):
    """ID-based readout; owned by the header toggle before editor destruction."""

    def __init__(self, panel, *, sidebar, fields, studio):
        super().__init__(panel._workspace_context_toggle)
        self._panel = panel
        self._sidebar = sidebar
        self._fields = fields
        self._studio = studio
        self._session = panel._session
        self._track_id = ""
        self._clip_id = ""
        self._narrow = None
        self._restore_context = True
        panel.track_selected.connect(self.inspect_track)
        panel.clip_selected.connect(self.inspect_clip)
        panel.ctrl.clip_changed.connect(self.clip_changed)
        sidebar.installEventFilter(self)
        panel._workspace_splitter.installEventFilter(self)
        panel._workspace_context_toggle.toggled.connect(self.toggle_context)
        studio.clicked.connect(self.open_studio)
        for action in panel._workspace_zoom_actions:
            action.triggered.connect(self.zoom_preset)
        self.refresh()

    @pyqtSlot(str)
    def inspect_track(self, track_id):
        self._track_id = track_id
        self._clip_id = ""
        self.refresh()

    @pyqtSlot(str)
    def inspect_clip(self, clip_id):
        self._clip_id = clip_id
        self.refresh()

    @pyqtSlot(str)
    def clip_changed(self, clip_id):
        if clip_id == self._clip_id:
            self.refresh()

    @pyqtSlot()
    def refresh(self):
        panel = self._panel
        if self._session is not panel._session:
            self._session = panel._session
            self._track_id = self._clip_id = ""
        track, clip = (panel.ctrl._find_track_and_clip(self._clip_id)
                       if self._clip_id else (None, None))
        if clip is None:
            self._clip_id = ""
            track = panel._session.get_track(self._track_id) if self._track_id else None
        else:
            self._track_id = track.id
        if track is None:
            self._track_id = ""
        values = _track_values(track, panel._session)
        values.update(_clip_values(clip))
        for key, text in values.items():
            field = self._fields[key]
            if field.text() != text:
                field.setText(text)
                field.setToolTip(text)
        self._studio.setEnabled(clip is not None)

    @pyqtSlot()
    def open_studio(self):
        self.refresh()
        if self._clip_id:
            self._panel._on_clip_open_processing_studio(self._clip_id)

    @pyqtSlot()
    def zoom_preset(self):
        seconds = self.sender().data()
        panel = self._panel
        zoom = px_per_sec_for_span(seconds, panel._clip_area._viewport.width(),
                                   panel.ctrl.px_per_sec)
        panel.set_zoom_centered(zoom)

    @pyqtSlot(bool)
    def toggle_context(self, visible):
        self._restore_context = visible
        self._sidebar.setVisible(visible)
        if visible:
            self.refresh()

    def eventFilter(self, watched, event):
        if watched is self._sidebar and event.type() == QEvent.Type.Show:
            self.refresh()
        if watched is self._panel._workspace_splitter and event.type() == QEvent.Type.Resize:
            self._sync_context_width()
        return super().eventFilter(watched, event)

    def _sync_context_width(self):
        narrow = self._panel._workspace_splitter.width() < 800
        if narrow == self._narrow:
            return
        self._narrow = narrow
        visible = self._restore_context and not narrow
        toggle = self._panel._workspace_context_toggle
        with QSignalBlocker(toggle):
            toggle.setChecked(visible)
        self._sidebar.setVisible(visible)


def build_workspace(panel):
    """Create the original controls once; Qt layouts/splitter own every widget."""
    root = QVBoxLayout(panel)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    root.addWidget(_build_header(panel))
    splitter = QSplitter(Qt.Orientation.Horizontal, panel)
    splitter.setHandleWidth(4)
    panel._workspace_splitter = splitter
    splitter.addWidget(_build_editor(panel))
    sidebar, fields, studio = _build_context(panel)
    splitter.addWidget(sidebar)
    splitter.setCollapsible(0, False)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)
    splitter.setSizes([960, 240])
    root.addWidget(splitter, 1)
    panel._workspace_context = _WorkspaceContext(
        panel, sidebar=sidebar, fields=fields, studio=studio)
