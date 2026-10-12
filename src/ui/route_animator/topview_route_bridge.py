# -*- coding: utf-8 -*-
"""TopView「画轨道选设备」→ 跑动编排 直通桥（R180 待办#12 断链修复）。

链路：
  topview_track_draw.finish() 捕获后
    → 本桥 present() 把报价（页 id + 有序设备 id + 设备中心点序列）挂到
      TopView 画布顶部的非模态浮条（_RouteOfferToast，「应用为跑动路线」）
    → 点击后经 ``route_apply_requested`` Qt 信号送达主窗
    → ``deliver_to_runner`` 走既有 ``_open_route_animator`` 单例入口打开/复用
      跑动编排窗口 → ``RouteRunnerPanel.apply_topview_route``
      → 内部仍走 ``canvas.track_chain_drawn`` → ``_on_track_drawn`` →
      平滑 → ``engine.add_track``（与画布手画/网格建造同一契约，引擎零改动）。

接线约定（上帝类零逻辑）：
  * install_track_tool() 在画布上建桥并存 ``canvas._route_bridge``；
  * main_window 侧只 connect：``_open_route_animator`` 里 3 行调
    :func:`wire_route_bridge`（幂等）；
  * 若用户从没开过跑动窗口就点浮条，点击瞬间桥自身懒发现主窗
    （``find_route_main_window`` 扫 topLevelWidgets 找 ``_open_route_animator``），
    补齐同一个 connect —— 双路径都收敛到 wire()，幂等防重连。

浮条生命周期：finish 报价 → 超时(12s)/×钮/Esc/新笔画起笔/应用成功 → 消失；
Esc 与取消语义走 ``topview_track_draw.cancel`` 调用方同步 dismiss。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton

from ..qascade_theme import (
    ACTIVE_ORANGE, BG_EDITOR, BORDER_NORMAL, SELECT_BLUE, TEXT_PRIMARY,
    TEXT_SECONDARY, TEXT_SELECTED_FG,
)
from ..main_icon_library import load_tabler_icon, load_tabler_pixmap

logger = logging.getLogger(__name__)

#: 浮条自动收起时长（毫秒）——非模态报价不该常驻遮画布。
ROUTE_OFFER_TIMEOUT_MS = 12000

Point = Tuple[float, float]


def _toast_qss() -> str:
    """浮条皮肤：全部走 qascade_theme 令牌（禁裸色值）。"""
    return (
        f"#TopViewRouteToast{{background:{BG_EDITOR};"
        f"border:1px solid {BORDER_NORMAL};border-radius:6px;}}"
        f"#TopViewRouteToast QLabel{{color:{TEXT_PRIMARY};"
        f"background:transparent;border:none;}}"
        f"#TopViewRouteToast QPushButton{{background:{SELECT_BLUE};"
        f"color:{TEXT_SELECTED_FG};border:1px solid {SELECT_BLUE};"
        f"border-radius:4px;padding:2px 10px;}}"
        f"#TopViewRouteToast QPushButton:disabled{{color:{TEXT_SECONDARY};"
        f"background:{BG_EDITOR};border-color:{BORDER_NORMAL};}}"
        f"#TopViewRouteToast QToolButton{{background:transparent;border:none;}}"
    )


class _RouteOfferToast(QFrame):
    """画布顶部居中浮条：捕获摘要 + 「应用为跑动路线」+ 关闭钮。

    普通子控件（不吃 WA_TransparentForMouseEvents）——用户可点；只遮顶部一条，
    12 秒自隐，不阻断画布交互。
    """

    apply_clicked = pyqtSignal()

    def __init__(self, canvas: QObject) -> None:
        super().__init__(canvas)
        self.setObjectName("TopViewRouteToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_toast_qss())
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(8)
        icon = QLabel(self)
        icon.setPixmap(load_tabler_pixmap("route", 14, ACTIVE_ORANGE))
        lay.addWidget(icon)
        self._label = QLabel(self)
        lay.addWidget(self._label)
        self._apply_btn = QPushButton("应用为跑动路线", self)
        self._apply_btn.setIcon(load_tabler_icon("route", 13, TEXT_SELECTED_FG))
        self._apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_btn.clicked.connect(self.apply_clicked)
        lay.addWidget(self._apply_btn)
        close = QToolButton(self)
        close.setIcon(load_tabler_icon("x", 12, TEXT_SECONDARY))
        close.setAutoRaise(True)
        close.setFixedSize(20, 20)
        close.setToolTip("关闭（Esc 同效）")
        close.clicked.connect(self.hide)
        lay.addWidget(close)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    def set_offer(self, count: int, can_apply: bool) -> None:
        """刷新摘要文案与「应用」钮可用态（<2 台时禁用并给出原因）。"""
        self._label.setText(f"已捕获 {count} 台 · 按轨迹顺序编号")
        self._apply_btn.setEnabled(bool(can_apply))
        self._apply_btn.setToolTip(
            "" if can_apply else "跑动路线至少需要 2 台设备，请把线画过更多设备")

    def relayout(self) -> None:
        """随画布尺寸重定位（顶部居中，偏离描画主区域）。"""
        host = self.parentWidget()
        if host is None:
            return
        self.adjustSize()
        self.move(max(6, (host.width() - self.width()) // 2), 8)

    def show_offer(self) -> None:
        """弹出报价并重置自动收起计时。"""
        self.relayout()
        self.show()
        self.raise_()
        self._hide_timer.start(ROUTE_OFFER_TIMEOUT_MS)


def find_route_main_window() -> Optional[QObject]:
    """懒发现主窗：顶层控件里带 ``_open_route_animator`` 入口的那个。"""
    app = QApplication.instance()
    if app is None:
        return None
    for w in app.topLevelWidgets():
        if hasattr(w, "_open_route_animator"):
            return w
    return None


def _open_runner(main_window: QObject) -> Optional[QObject]:
    """打开/复用跑动编排窗口；失败或入口缺失返回 None（调用方静默降级）。"""
    opener = getattr(main_window, "_open_route_animator", None)
    if not callable(opener):
        return None
    try:
        opener()
    except Exception:
        logger.debug("topview_route_bridge: open runner failed", exc_info=True)
        return None
    return getattr(main_window, "_route_animator_window", None)


def deliver_to_runner(main_window: QObject, payload: dict) -> None:
    """浮条确认的投递端：打开/复用跑动窗口，把点序喂给它。

    只调既有公开接口（``_open_route_animator`` 单例 + runner 面板新增的
    ``apply_topview_route``），主窗/面板里不落任何业务逻辑。
    """
    win = _open_runner(main_window)
    apply_fn = getattr(win, "apply_topview_route", None) if win is not None else None
    if not callable(apply_fn):
        return
    try:
        apply_fn(page_id=str((payload or {}).get("page_id") or ""),
                 points=(payload or {}).get("points") or [])
    except Exception:
        logger.debug("topview_route_bridge: apply_topview_route failed", exc_info=True)


def present_route_offer(panel: object, canvas: object,
                        ordered: List[str],
                        centers: Dict[str, Point]) -> None:
    """finish() 收笔报价入口：把本次捕获挂到画布浮条桥（无桥时静默）。

    从 ``topview_track_draw`` 下沉（门禁债：该文件行数超限）——桥的
    报价语义归桥模块，描画模块只管捕获与写回。
    """
    bridge = getattr(canvas, "_route_bridge", None)
    if bridge is None:
        return
    pts = [centers[fid] for fid in ordered if fid in centers]
    getter = getattr(panel, "current_page", None)
    page = getter() if callable(getter) else {}
    page_id = str(page.get("id", "") or "") if isinstance(page, dict) else ""
    bridge.present(page_id, ordered, pts)


def wire_route_bridge(bridge: "TopViewRouteBridge", main_window: QObject) -> bool:
    """main_window 侧接线入口（幂等）：bridge 信号 → deliver_to_runner。"""
    if bridge is None:
        return False
    return bridge.wire(main_window)


class TopViewRouteBridge(QObject):
    """TopView 侧报价桥：持浮条 + 待发负载 + ``route_apply_requested`` 信号。

    创建方：``topview_track_draw.install_track_tool``（存 ``canvas._route_bridge``）。
    消费方：``wire()``/``_autowire()`` 接到的主窗 ``deliver_to_runner``，
    或测试里直接 connect ``route_apply_requested``。
    """

    route_apply_requested = pyqtSignal(dict)

    def __init__(self, canvas: QObject) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._pending: Optional[Dict[str, object]] = None
        self._wired_window: Optional[QObject] = None
        self._wired_conn = None
        self._toast = _RouteOfferToast(canvas)
        self._toast.apply_clicked.connect(self._on_apply_clicked)

    # ── 报价生命周期 ────────────────────────────────────────────────
    def present(self, page_id: str, ordered_ids: List[str], points) -> None:
        """finish() 调用：挂新报价并弹浮条（空捕获不报价）。"""
        ids = [str(i) for i in (ordered_ids or []) if str(i)]
        pts = self._normalize_points(points)
        if not ids:
            return
        self._pending = {"page_id": str(page_id or ""),
                         "ordered_ids": ids, "points": pts}
        self._autowire()
        self._toast.set_offer(len(ids), len(pts) >= 2)
        self._toast.show_offer()

    @staticmethod
    def _normalize_points(points) -> List[Point]:
        """容忍 (x,y) 序列/列表/QPointF 之外的畸形项 —— 丢弃不崩。"""
        out: List[Point] = []
        for p in (points or []):
            try:
                out.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def dismiss(self) -> None:
        """丢弃报价：Esc/×钮/新笔画起笔/应用后统一走这里。"""
        self._pending = None
        self._toast.hide()

    def relayout(self) -> None:
        """画布 Resize 时随动（由 TrackToolFilter 的 Resize 分支调）。"""
        if self._toast.isVisible():
            self._toast.relayout()

    # ── 接线 ────────────────────────────────────────────────────────
    def wire(self, main_window: QObject) -> bool:
        """把信号接到指定主窗（幂等；换窗先拆旧连接）。"""
        if main_window is None or not hasattr(main_window, "_open_route_animator"):
            return False
        if self._wired_window is main_window:
            return True
        self._unwire()
        self._wired_conn = self.route_apply_requested.connect(
            lambda payload, _mw=main_window: deliver_to_runner(_mw, payload))
        self._wired_window = main_window
        return True

    def _unwire(self) -> None:
        if self._wired_conn is not None:
            try:
                self.route_apply_requested.disconnect(self._wired_conn)
            except (TypeError, RuntimeError):
                pass
            self._wired_conn = None
        self._wired_window = None

    def _autowire(self) -> None:
        """未接线时按 topLevelWidgets 懒发现主窗（用户先画轨道没开过跑动窗口）。"""
        if self._wired_window is not None:
            return
        mw = find_route_main_window()
        if mw is not None:
            self.wire(mw)

    # ── 发射 ────────────────────────────────────────────────────────
    def _on_apply_clicked(self) -> None:
        """浮条「应用为跑动路线」→ 发信号并收起（无人接则静默，等价无窗环境）。"""
        payload = self._pending
        self.dismiss()
        if payload:
            self.route_apply_requested.emit(dict(payload))
