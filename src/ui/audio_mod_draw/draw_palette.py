# -*- coding: utf-8 -*-
"""Draw 大页左栏源卡库（R182）—— 分组卡片（源卡 + 预设模板 + 规则卡通道）。

点击条目 = 在当前视图中心建卡；按住拖出 = QDrag 拖入画布落点建卡。
数据唯一来源 = `draw_cards.SOURCE_CATALOG / TEMPLATE_CATALOG /
default_rule_types`（勿自建第二份清单）。
"""
from __future__ import annotations

from PyQt6.QtCore import QMimeData, QPoint, Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                             QVBoxLayout, QWidget)

from ..qascade_theme import (
    BG_PANEL,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ..widget_kit import PanelSection
from ..palette_operating_preferences import bind_draw_preferences
from ..main_icon_library import load_tabler_icon
from .draw_cards import SOURCE_CATALOG, TEMPLATE_CATALOG, ch_label, type_label
from .draw_effect_grid import EffectGrid
from .draw_effects import GROUP_ICONS, effect_choices
# R183-H：源卡/模板 B0 美术配对 + 缩略加载（拆出模块，见 draw_palette_art）
from .draw_palette_art import art_id_for_payload, load_art_pixmap_16, translucent_grab
# R183-H：预设库区拆出（draw_preset_section），此处 re-export 保旧导入路径不变
from .draw_preset_section import PresetRow, PresetSection  # noqa: F401

__all__ = ["SourcePalette", "MIME_CARD", "make_card_mime", "PaletteEntry",
           "PresetRow", "PresetSection", "art_id_for_payload"]

MIME_CARD = "application/x-qascade-audio-mod-card"   # 值 = "source:<key>" 等
_ENTRY_H = 38


def make_card_mime(payload: str) -> QMimeData:
    """条目 payload → QDrag mime（唯一构造点；测试直接断言内容）。"""
    mime = QMimeData()
    mime.setData(MIME_CARD, str(payload).encode("utf-8"))
    return mime


def load_pixmap_16(name: str, color: str = TEXT_PRIMARY):
    """16px tabler pixmap（失败 → 空 pixmap 不阻塞布局）。"""
    try:
        return load_tabler_icon(name, 16, color).pixmap(16, 16)
    except Exception:
        return None


class PaletteEntry(QFrame):
    """单条目：图标 + 名称 + 一句话说明；点击=建卡，按住拖出=拖入落点建卡。

    R183-H：``art_id`` 给出且渲染成功时，16px 槽位改画 B0 美术缩略（不染色块、
    不位移）；否则照旧 tabler 图标 —— 旧构造点（4 位置参）行为不变。
    """

    drag_started = pyqtSignal(str)
    clicked_entry = pyqtSignal(str)

    def __init__(self, payload: str, name: str, desc: str, icon: str, *,
                 art_id: str = "", parent=None):
        super().__init__(parent)
        self._payload = str(payload)
        self._name = str(name)
        self._desc = str(desc)
        self._art_id = str(art_id or "")
        self._press_pos = None
        self.setFixedHeight(_ENTRY_H)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(desc)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 6, 2)
        lay.setSpacing(7)
        icon_label = QLabel()
        pm = load_art_pixmap_16(self._art_id) or load_pixmap_16(icon)
        if pm is not None and not pm.isNull():
            icon_label.setPixmap(pm)
        icon_label.setFixedSize(16, 16)
        icon_label.setStyleSheet("background: transparent;")
        lay.addWidget(icon_label)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        head = QLabel(self._name)
        head.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px;"
                           "background: transparent; border: none;")
        sub = QLabel(self._desc.split("。")[0])
        sub.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;"
                          "background: transparent; border: none;")
        text.addWidget(head)
        text.addWidget(sub)
        lay.addLayout(text, 1)
        self.setStyleSheet(
            "PaletteEntry { background: transparent; border: none; border-radius: 4px; }"
            f"PaletteEntry:hover {{ background: {BG_PANEL}; }}")

    def payload(self) -> str:
        return self._payload

    def art_id(self) -> str:
        """B0 美术通道 id（无 → ""；R183-H 测试/面板断言用）。"""
        return self._art_id

    def label(self) -> str:
        """条目显示名（搜索/测试断言用）。"""
        return self._name

    def matches(self, query: str) -> bool:
        q = (query or "").strip().lower()
        if not q:
            return True
        hay = f"{self._name} {self._desc} {self._payload}".lower()
        return q in hay

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.position()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if (self._press_pos is not None and ev.buttons() & Qt.MouseButton.LeftButton
                and (ev.position() - self._press_pos).manhattanLength() > 6):
            self._press_pos = None
            self.drag_started.emit(self._payload)
            drag = QDrag(self)
            drag.setMimeData(make_card_mime(self._payload))
            pm = translucent_grab(self)       # B1：半透明条目影像（同 honeycomb 手法）
            if pm is not None:
                drag.setPixmap(pm)
                drag.setHotSpot(ev.position().toPoint())
            drag.exec(Qt.DropAction.CopyAction)
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        pressed, self._press_pos = self._press_pos, None
        if pressed is not None and ev.button() == Qt.MouseButton.LeftButton:
            self.clicked_entry.emit(self._payload)
        super().mouseReleaseEvent(ev)


class SourcePalette(QWidget):
    """左栏：搜索 + 信号源/模板小列表 + **效果库蜂巢网格**（R182）。

    用户规格（2026-09-29）：效果选择区用蜂巢瓦片网格（同调度台存储区），
    不再纵向长列表。信号源/模板条目少（共 8 条）保留小列表。
    """

    drag_started = pyqtSignal(str)
    entry_clicked = pyqtSignal(str)
    #: M4 用户预设库信号（面板接线：保存/加载/导入/导出）
    preset_save_requested = pyqtSignal(str)
    preset_load_requested = pyqtSignal(str)
    preset_import_requested = pyqtSignal()
    preset_export_requested = pyqtSignal()
    preset_rename_requested = pyqtSignal(str, str)
    preset_delete_requested = pyqtSignal(str)

    def __init__(self, rule_types=None, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(230)
        self._entries: list = []
        self._rule_entries: list = []
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索效果/信号…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        v.addWidget(self.search)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(4)
        self._sections: list = []
        body_lay.addWidget(self._section("信号源", self._source_items()))
        body_lay.addWidget(self._section("预设模板", self._template_items()))
        # M4 用户预设库：命名组合存/取/导入/导出（不入 _sections，避免搜索
        # 全隐藏；自管过滤与空态）
        self._preset_section = PanelSection("预设库", expanded=True)
        self._presets = PresetSection()
        self._presets.save_requested.connect(self.preset_save_requested)
        self._presets.load_requested.connect(self.preset_load_requested)
        self._presets.import_requested.connect(self.preset_import_requested)
        self._presets.export_requested.connect(self.preset_export_requested)
        self._presets.rename_requested.connect(self.preset_rename_requested)
        self._presets.delete_requested.connect(self.preset_delete_requested)
        self._preset_section.add_widget(self._presets)
        body_lay.addWidget(self._preset_section)
        # R182 效果库：85 种音频效果 → 蜂巢网格（唯一数据源 draw_effects）
        fx_sec = PanelSection("效果库", expanded=True)
        self._grid = EffectGrid()
        grp_icon = dict(GROUP_ICONS)
        self._grid.set_effects([
            (key, name, group, icon)
            for key, name, group, icon, _desc in effect_choices()])
        self._grid.activated.connect(
            lambda key: self.entry_clicked.emit(f"fx:{key}"))
        # ★ R182 审计修：效果瓦片拖拽链——网格只发信号，此处自起 QDrag
        # （修前链路无人构造 QDrag，"按住拖出"手势彻底无效果）。
        self._grid.drag_started.connect(
            lambda key: self._exec_grid_drag(key))
        fx_sec.add_widget(self._grid)
        body_lay.addWidget(fx_sec)
        self._fx_section = fx_sec
        self._sections.append(fx_sec)
        body_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        v.addWidget(scroll, 1)
        bind_draw_preferences(self)

    def effect_grid(self):
        """效果库网格控件（面板/测试直接访问）。"""
        return self._grid

    def _exec_grid_drag(self, key: str) -> None:
        """效果瓦片按住拖出：QDrag(mime="fx:<key>") → 画布 dropEvent 建卡
        （R183 B3：半透明瓦片影像）。"""
        self.drag_started.emit(f"fx:{key}")
        drag = QDrag(self._grid)
        drag.setMimeData(make_card_mime(f"fx:{key}"))
        pm = self._grid.make_drag_pixmap(str(key))
        if pm is not None:
            drag.setPixmap(pm)
            _dpr = pm.devicePixelRatio() or 1.0   # 清查③②D：逻辑像素热点
            drag.setHotSpot(QPoint(int(round(pm.width() / _dpr / 2.0)),
                                   int(round(pm.height() / _dpr / 2.0))))
        drag.exec(Qt.DropAction.CopyAction)

    def _section(self, title: str, items) -> PanelSection:
        sec = PanelSection(title, expanded=len(self._sections) < 2)
        for spec in items:
            self._add_entry(sec, spec, self._entries)
        self._sections.append(sec)
        return sec

    def _add_entry(self, sec, spec: tuple, bucket: list) -> PaletteEntry:
        """spec = (payload, name, desc, icon)；连信号 + 入桶（art id 由 payload 解析）。"""
        payload, name, desc, icon = spec
        entry = PaletteEntry(payload, name, desc, icon,
                             art_id=art_id_for_payload(payload))
        entry.drag_started.connect(self.drag_started.emit)
        entry.clicked_entry.connect(self.entry_clicked.emit)
        sec.add_widget(entry)
        bucket.append(entry)
        return entry

    def set_rule_types(self, rule_types) -> None:
        """兼容空操作（R182 重构：规则卡组已被常驻效果库取代）。

        保留签名供旧调用点（面板 set_rule_types(scope)）继续工作；
        `rule_types` 当前仅存储备用（效果卡目标由卡上勾选决定）。
        """
        self._last_rule_types = list(rule_types or [])

    @staticmethod
    def _source_items() -> list:
        return [(f"source:{key}", spec[0], spec[2], spec[3])
                for key, spec in SOURCE_CATALOG.items()]

    @staticmethod
    def _template_items() -> list:
        return [(f"template:{key}", f"{name}（预设）", desc, "confetti")
                for key, (name, _f, _p, desc) in TEMPLATE_CATALOG.items()]

    def apply_filter(self, query: str) -> None:
        """按名称/说明/payload 过滤条目；组内全隐藏则收起整组。

        R182：效果库网格同步过滤（名称/分组/key 子串）。
        M4：预设库分区按名过滤（有查询且无命中 → 收起分区）。
        R183-H：效果库分区显隐见 `_sync_fx_visibility`（子件是自绘网格，不参与
        "无条目即收起"计数）。
        """
        try:
            self._grid.apply_filter(query)
        except AttributeError:
            pass
        q = (query or "").strip()
        try:
            hits = self._presets.apply_filter(q)
            self._preset_section.setVisible((not q) or hits > 0)
        except AttributeError:
            pass
        self._sync_fx_visibility(q)
        fx = getattr(self, "_fx_section", None)
        for sec in self._sections:
            if sec is fx:
                continue
            visible = 0
            for entry in sec.findChildren(PaletteEntry):
                show = entry.matches(query)
                entry.setVisible(show)
                visible += 1 if show else 0
            sec.setVisible(visible > 0)

    def _sync_fx_visibility(self, q: str) -> None:
        """效果库分区恒可见（R183-H）。

        子件是自绘 `EffectGrid`（无 `PaletteEntry` 子件），不参与"无条目即收起"
        规则——修前任意输入都会把整段效果库永久隐藏（命中亦不可见）。零命中由
        网格自身出 B0 插画空态（"没有匹配的条目"）。``q`` 保留参数供将来区分。
        """
        _ = q
        fx = getattr(self, "_fx_section", None)
        if fx is not None:
            fx.setVisible(True)

    def set_presets(self, names) -> None:
        """刷新用户预设库列表（面板保存/导入/启动时调用）。"""
        self._presets.set_presets(names)

    def preset_names(self) -> list:
        """当前预设库列表显示名（测试/面板断言）。"""
        return self._presets.preset_names()

    def preset_section(self):
        """预设库分区控件（面板/测试直接访问）。"""
        return self._presets

    def entries(self) -> list:
        """全部条目（测试断言目录覆盖与过滤）。"""
        return list(self._entries)


def build_entry(payload: str, name: str, desc: str, icon: str) -> PaletteEntry:
    """单条目工厂（面板/测试共用；图标名未知时回退通用 glyph）。"""
    return PaletteEntry(payload, name, desc, icon,
                        art_id=art_id_for_payload(payload))


def source_feature_of(key: str) -> str:
    """源卡目录 key → feature（面板建卡用；未知 → beat_strength）。"""
    spec = SOURCE_CATALOG.get(str(key))
    return str(spec[1]) if spec else "beat_strength"


def template_spec_of(key: str) -> tuple:
    """模板 key → (feature, phase)（未知 → ("", 0.0)）。"""
    spec = TEMPLATE_CATALOG.get(str(key))
    return (str(spec[1]), float(spec[2])) if spec else ("", 0.0)


def effect_key_of_payload(payload: str) -> str:
    """效果卡 payload（"fx:<key>"）→ 效果 key（非效果 → ""）。"""
    head, _, tail = str(payload).partition(":")
    return tail if head in ("fx", "effect") else ""


def ch_type_of_payload(payload: str) -> str:
    """规则卡 payload（"rule:pump"）→ 通道类型（非规则 → ""）。"""
    head, _, tail = str(payload).partition(":")
    return tail if head == "rule" else ""


def rule_label(ch_type: str) -> str:
    """通道中文短标签（面板摘要）。"""
    return ch_label(ch_type)
