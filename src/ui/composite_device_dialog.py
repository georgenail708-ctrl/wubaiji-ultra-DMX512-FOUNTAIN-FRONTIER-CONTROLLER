# -*- coding: utf-8 -*-
"""按类型虚拟合并对话框（导入配接表后、生成 TopView 前弹出；亦可独立调用）。

「合并」是**宏观创建组合设备的规律**：把「A类型(名称)」和「B类型」合并 → 定义出一种
**全新的虚拟设备类型**。生成 TopView 时按规律把各类型的真实实例**按下标配对**
（A类#i + B类#i …），每一对就是这种新类型的一台，显示成一个点。合并后被吃掉的原
类型不再单独显示。

只影响显示与选择；输出仍分别落到各真实通道。可整体跳过。result_logical_devices()
返回 LogicalDevice.to_dict() 列表（全部 kind="composite"）。
"""
from __future__ import annotations

from typing import List, Dict, Tuple, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QDialogButtonBox, QMessageBox, QWidget, QCheckBox,
    QComboBox, QInputDialog,
)

from ..core.models import LogicalDevice
from . import qascade_theme as _th
from .widget_kit import (  # D 波任务 A/B：浮窗解剖标准件
    EmptyState, PanelSection, apply_dialog_chrome, prop_row,
)


def _kit_title_row(title: str, on_close) -> QWidget:
    """D 波任务 A：浮窗标准标题行——24px 高，标题 12px 左、✕ 22px 右。"""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    t = QLabel(title)
    t.setStyleSheet(
        f"color:{_th.TEXT_PRIMARY}; font-size:12px; font-weight:bold;"
        f"background:transparent;")
    h.addWidget(t)
    h.addStretch(1)
    x = QPushButton("✕")
    x.setFixedSize(22, 22)
    x.setCursor(Qt.CursorShape.PointingHandCursor)
    x.setToolTip("关闭")
    x.setStyleSheet(
        f"QPushButton{{background:transparent;border:none;border-radius:3px;"
        f"color:{_th.TEXT_SECONDARY};font-size:11px;padding:0px;}}"
        f"QPushButton:hover{{background:{_th.BG_BUTTON_HOVER};"
        f"color:{_th.TEXT_PRIMARY};}}")
    x.clicked.connect(on_close)
    h.addWidget(x)
    row.setFixedHeight(24)
    return row

# 2026-08-27 D 波：原列表内联外观 QSS 与全局主题重复，按 §3 删除；
# 列表底色/边框/选中态统一走 GLOBAL_QSS。

# 灯参考对象优先级：电机 > 阀 > 泵 > 其它（多候选时弹窗让用户选）
_REF_PRIORITY = ["motor", "valve", "pump", "other"]


def assign_lights_by_proximity(
    anchors: List[Tuple[str, float, float]],
    lights: List[Tuple[str, float, float]],
    n_per_anchor: int,
) -> Tuple[List[List[str]], List[str]]:
    """把灯按**空间就近**分配给锚设备：每个锚绑定最近的 n 盏未占用灯。

    anchors/lights: [(fid, x, y)]。round-robin 轮流分配——每轮每个锚领取离它最近的
    一盏尚未占用的灯，共 n 轮，使分配均衡、每盏灯只配一次。

    返回 (assignments, leftover_light_ids)：
      assignments[i] = 第 i 个锚绑定的灯 fid 列表（≤ n_per_anchor）。
      leftover_light_ids = 没分到任何锚的灯（灯多于 锚×n 时）。
    """
    n = max(0, int(n_per_anchor))
    assigns: List[List[str]] = [[] for _ in anchors]
    if not anchors or n == 0:
        return assigns, [str(l[0]) for l in lights]
    remaining = {str(fid): (float(x), float(y)) for fid, x, y in lights}
    for _round in range(n):
        if not remaining:
            break
        for ai, (afid, ax, ay) in enumerate(anchors):
            if not remaining:
                break
            if len(assigns[ai]) > _round:
                continue  # 该锚本轮已分（成员数不等容错）
            best_fid = None
            best_d = None
            ax = float(ax); ay = float(ay)
            for lfid, (lx, ly) in remaining.items():
                d = (lx - ax) ** 2 + (ly - ay) ** 2
                if best_d is None or d < best_d:
                    best_d = d; best_fid = lfid
            if best_fid is not None:
                assigns[ai].append(best_fid)
                remaining.pop(best_fid, None)
    return assigns, list(remaining.keys())


class CompositeDeviceDialog(QDialog):
    def __init__(self, fixtures_by_type: Dict[str, List[Tuple[str, str]]],
                 positions: Optional[Dict[str, Tuple[float, float]]] = None,
                 light_types: Optional[set] = None,
                 type_roles: Optional[Dict[str, str]] = None, parent=None):
        """fixtures_by_type: {类型名: [(fixture_id, 显示名), ...]}。
        positions: {fixture_id: (x, y)} 真实坐标，供「配灯就近分配」。
        light_types: 含灯通道(RGBW)的类型名集合，用于识别哪一类是灯（默认灯选择）。
        type_roles: {类型名: 'motor'|'valve'|'pump'|'light'|'other'}，定灯参考对象优先级。
        """
        super().__init__(parent)
        # 拷贝一份可消耗的类型表（被合并的类型从中移除）
        self._fixtures_by_type = {t: list(items) for t, items in fixtures_by_type.items() if items}
        self._positions = dict(positions or {})
        self._light_types = set(light_types or set())
        self._type_roles = dict(type_roles or {})
        self._composites: List[LogicalDevice] = []
        self.setWindowTitle("按类型虚拟合并")
        self.setMinimumSize(560, 600)
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)   # 任务 A：内容边距 12
        root.setSpacing(8)
        root.addWidget(_kit_title_row("按类型虚拟合并", self._on_skip))

        intro = QLabel("① 给每种设备类型选角色（灯/电机/阀/泵/其它）。\n"
                       "② 勾选要合并的类型 → 合并：非灯类型按下标配对成一台（泵#i+阀#i+电机#i…）；\n"
                       "   灯类型最多 1 种，每个锚组按**空间就近**绑定 N 盏灯，灯位置参考"
                       "电机>阀>泵优先级的对象（多候选会让你选）。")
        intro.setWordWrap(True)
        intro.setProperty("hint", True)   # 浮窗 QSS 弱化说明样式
        root.addWidget(intro)

        sec_types = PanelSection("设备类型（勾选合并 + 选角色）")
        self._type_list = QListWidget()
        sec_types.add_widget(self._type_list)
        root.addWidget(sec_types, 1)
        self._reload_type_list()

        name_w = QWidget()
        name_h = QHBoxLayout(name_w)
        name_h.setContentsMargins(0, 0, 0, 0)
        name_h.setSpacing(8)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("如：气爆+灯 一体组")
        name_h.addWidget(self._name_edit, 1)
        b_make = QPushButton("合并选中类型")
        b_make.setToolTip("把勾选的≥2 种类型按下标配对合并为一种新虚拟设备类型")
        b_make.setCursor(Qt.CursorShape.PointingHandCursor)
        b_make.clicked.connect(self._make_type_merge)
        name_h.addWidget(b_make)
        root.addWidget(prop_row("新类型名称", name_w))

        sec_done = PanelSection("已定义的合并类型")
        self._composite_list = QListWidget()
        self._composite_list.setMaximumHeight(120)
        sec_done.add_widget(self._composite_list)
        self._empty_cmp = EmptyState("尚无合并类型", "勾选上方设备类型并点击「合并选中类型」",
                                     icon_key="puzzle")
        self._empty_cmp.setVisible(True)   # 初始为空态
        sec_done.add_widget(self._empty_cmp)
        b_del = QPushButton("撤销选中的合并")
        b_del.setToolTip("从已定义列表移除选中的合并类型（重开对话框可重新合并）")
        b_del.setCursor(Qt.CursorShape.PointingHandCursor)
        b_del.clicked.connect(self._del_composite)
        sec_done.add_widget(b_del)
        root.addWidget(sec_done)

        bb = QDialogButtonBox()
        ok = bb.addButton("确认", QDialogButtonBox.ButtonRole.AcceptRole)
        skip = bb.addButton("跳过", QDialogButtonBox.ButtonRole.RejectRole)
        ok.setObjectName("primary")          # 任务 A：确定=primary 蓝底
        skip.setProperty("role", "cancel")  # 取消=cancel 次按钮
        ok.clicked.connect(self.accept)
        skip.clicked.connect(self._on_skip)
        bb.setCenterButtons(False)          # 按钮行右对齐
        root.addWidget(bb)
        apply_dialog_chrome(self)

    _ROLE_OPTIONS = [("other", "其它"), ("light", "灯"), ("motor", "电机"), ("valve", "阀"), ("pump", "泵")]

    def _reload_type_list(self):
        """重建类型行：勾选框 + 台数 + 角色下拉（用户分类）。已合并消耗的类型不再出现。"""
        self._type_list.clear()
        self._type_checks: List[Tuple[QCheckBox, str]] = []
        self._role_combos: Dict[str, QComboBox] = {}
        for tname, items in self._fixtures_by_type.items():
            it = QListWidgetItem(self._type_list)
            w = QWidget()
            lay = QHBoxLayout(w); lay.setContentsMargins(4, 2, 4, 2)
            cb = QCheckBox(f"{tname}")
            cnt = QLabel(f"{len(items)} 台")
            role = QComboBox()
            # 2026-08-27 D 波：行内 QLabel/QCheckBox 着色 QSS 与浮窗/全局主题
            # 重复，按 §3 删除（文字层级由 apply_dialog_chrome 提供）。
            for val, lbl in self._ROLE_OPTIONS:
                role.addItem(lbl, val)
            # 默认角色：用 type_roles（来自调用方的通道/名称推断）作初值，用户可改
            default_role = str(self._type_roles.get(tname, "other") or "other")
            idx = next((i for i, (v, _l) in enumerate(self._ROLE_OPTIONS) if v == default_role), 0)
            role.setCurrentIndex(idx)
            role.setFixedWidth(72)
            role.setCursor(Qt.CursorShape.PointingHandCursor)
            role_lbl = QLabel("角色")
            lay.addWidget(cb, 1); lay.addWidget(cnt); lay.addWidget(role_lbl); lay.addWidget(role)
            it.setSizeHint(w.sizeHint())
            self._type_list.addItem(it)
            self._type_list.setItemWidget(it, w)
            self._type_checks.append((cb, tname))
            self._role_combos[tname] = role

    def _role_of(self, tname: str) -> str:
        combo = self._role_combos.get(tname) if hasattr(self, "_role_combos") else None
        if combo is not None:
            return str(combo.currentData() or "other")
        return str(self._type_roles.get(tname, "other") or "other")

    def _selected_types(self) -> List[str]:
        return [t for cb, t in self._type_checks if cb.isChecked()]

    def _make_type_merge(self):
        types = self._selected_types()
        if len(types) < 2:
            QMessageBox.information(self, "按类型合并", "请至少勾选 2 种设备类型。")
            return
        # 灯 = 勾选中角色为 light 的类型（最多 1 种）
        light_sel = [t for t in types if self._role_of(t) == "light"]
        if len(light_sel) > 1:
            QMessageBox.information(self, "按类型合并", "灯类型最多只能有 1 种，请检查各类型角色。")
            return
        light_type = light_sel[0] if light_sel else ""
        anchor_types = [t for t in types if t != light_type]
        if not anchor_types:
            QMessageBox.information(self, "按类型合并", "除了灯，至少还要选 1 种非灯类型作锚。")
            return
        name = self._name_edit.text().strip() or "+".join(types)

        # 1) 非灯锚类型按下标配对成锚组（每组 = 各锚类型第 i 台）
        counts = [len(self._fixtures_by_type.get(t, [])) for t in anchor_types]
        n_groups = min(counts) if counts else 0
        if n_groups == 0:
            QMessageBox.information(self, "按类型合并", "锚类型没有可配对的实例。")
            return
        anchor_groups: List[List[str]] = []
        for i in range(n_groups):
            anchor_groups.append([str(self._fixtures_by_type[t][i][0]) for t in anchor_types])

        # 2) 有灯 → 选参考对象（电机>阀>泵>其它，多候选弹窗），按参考点就近配灯
        light_assign: List[List[str]] = [[] for _ in anchor_groups]
        leftover_lights: List[str] = []
        if light_type and self._positions:
            ref_type = self._pick_reference_type(anchor_types)
            if ref_type is None:
                return  # 用户取消选择
            ref_idx = anchor_types.index(ref_type)
            ref_pts = []
            for g in anchor_groups:
                rfid = g[ref_idx]
                x, y = self._positions.get(str(rfid), (0.0, 0.0))
                ref_pts.append((str(rfid), float(x), float(y)))
            lights = []
            for fid, _disp in self._fixtures_by_type.get(light_type, []):
                x, y = self._positions.get(str(fid), (0.0, 0.0))
                lights.append((str(fid), float(x), float(y)))
            # 每锚组配灯数 = 灯数 ÷ 锚组数（向上取整，吃光所有灯；倍数关系自定，无需用户设）
            n = max(1, -(-len(lights) // max(1, len(ref_pts)))) if ref_pts else 0
            light_assign, leftover_lights = assign_lights_by_proximity(ref_pts, lights, n)

        # 3) 组装 composite
        lds: List[LogicalDevice] = []
        for i, g in enumerate(anchor_groups):
            members = list(g) + list(light_assign[i])
            if len(members) < 2:
                continue
            lds.append(LogicalDevice(
                name=f"{name} #{i + 1}", member_fixture_ids=members,
                kind="composite", control_mode="merged", merge_group=name))
        if not lds:
            QMessageBox.information(self, "按类型合并", "没有可合并的实例。")
            return
        for ld in lds:
            self._composites.append(ld)
        item = QListWidgetItem(f"{name}（{len(lds)} 台）")
        item.setData(Qt.ItemDataRole.UserRole, [ld.id for ld in lds])
        self._composite_list.addItem(item)
        self._empty_cmp.setVisible(False)
        # 只移除真正被合进 composite 的实例：未配对的锚/灯（如灯少于锚组数时多出的锚）
        # 仍留在可选列表里，可再次参与合并，避免被静默吞掉。
        consumed = {str(fid) for ld in lds for fid in ld.member_fixture_ids}
        leftover_anchor = 0
        for t in types:
            rest = [(fid, disp) for fid, disp in self._fixtures_by_type.get(t, [])
                    if str(fid) not in consumed]
            if t in anchor_types:
                leftover_anchor += len(rest)
            if rest:
                self._fixtures_by_type[t] = rest
            else:
                self._fixtures_by_type.pop(t, None)
        self._reload_type_list()
        self._name_edit.clear()
        msg = f"已生成 {len(lds)} 台「{name}」（{len(anchor_types)} 类锚 × 下标配对）。"
        if light_type:
            used = sum(len(a) for a in light_assign)
            msg += f"\n按就近绑定 {used} 盏灯。"
            if leftover_lights:
                msg += f"剩余 {len(leftover_lights)} 盏灯未分配（灯多于 锚组×N），保持散台。"
        if leftover_anchor:
            msg += f"\n剩余 {leftover_anchor} 台锚未配对（保持散台，可再次合并）。"
        QMessageBox.information(self, "按类型合并", msg)

    def _pick_reference_type(self, anchor_types: List[str]) -> Optional[str]:
        """按优先级 电机>阀>泵>其它 选灯参考类型；同优先级多候选弹窗让用户选。

        返回选中的参考类型名；用户取消返回 None。"""
        role_of = {t: self._role_of(t) for t in anchor_types}
        for role in _REF_PRIORITY:
            cands = [t for t in anchor_types if role_of.get(t) == role]
            if not cands:
                continue
            if len(cands) == 1:
                return cands[0]
            # 多候选 → 弹窗
            label = {"motor": "电机", "valve": "阀", "pump": "泵", "other": "其它"}.get(role, role)
            choice, ok = QInputDialog.getItem(
                self, "选择灯参考对象",
                f"有多个「{label}」类型，灯位置参考哪一个？", cands, 0, False)
            if not ok:
                return None
            return choice
        return anchor_types[0] if anchor_types else None

    def _del_composite(self):
        it = self._composite_list.currentItem()
        if not it:
            return
        ids = set(it.data(Qt.ItemDataRole.UserRole) or [])
        self._composites = [c for c in self._composites if c.id not in ids]
        self._composite_list.takeItem(self._composite_list.row(it))
        self._empty_cmp.setVisible(self._composite_list.count() == 0)
        # 注：撤销后不自动恢复类型到列表（避免状态复杂）；重开对话框即可重新合并。

    def _on_skip(self):
        self._composites = []
        self.reject()

    # ── 结果 ───────────────────────────────────────────────────────────────────
    def result_logical_devices(self) -> List[dict]:
        """返回 LogicalDevice.to_dict() 列表（全部按类型合并产生的 composite）。"""
        out: List[dict] = []
        claimed: set = set()
        for ld in self._composites:
            members = [m for m in ld.member_fixture_ids if m not in claimed]
            if len(members) < 2:
                continue
            claimed.update(members)
            ld.member_fixture_ids = members
            out.append(ld.to_dict())
        return out
