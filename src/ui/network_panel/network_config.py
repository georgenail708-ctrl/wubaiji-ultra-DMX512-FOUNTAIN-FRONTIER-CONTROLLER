# -*- coding: utf-8 -*-
"""Qascade V1.0 — Network Config Panel（R182 前端重做：三区范式）。

页面结构：
  顶部  状态卡（协议 / 运行状态灯 / 绑定 IP / 输出目标摘要）
  中部  左 = 输出（预设 / 目标 IP / 宇宙 / 端口）  右 = 设备发现（ArtPoll 表）
  底部  操作行（应用配置 / 启动 / 停止 / 防火墙放行）+ 状态行
  折叠  协议与输入（默认关的输入监听）/ 跨设备排查提示

R182 变更（在 R181 基础上）：
  1. 顶部状态卡：读 NetworkManager **实际对象**（is_running / protocol_name /
     get_sender_status），修前状态行只读 ``_config``——配置改了没重启也显示
     "运行中 + 新配置"（P1-4 状态失真）；
  2. 两区并排（输出 | 发现）；按钮统一走 network_button_factory
     （22px / 11px / 14px 图标 / 令牌色，全页一套）；
  3. 一键放行复用后端 firewall_probe 的规则名与命令构造（带引号），
     提交后回探确认（P1-3 假成功）；
  4. 无效控件禁用 + tooltip（P1-10）；
  5. 状态行 info/warn 强制可见（P1-8）；
  6. 绑定失败红字（P1-5，NetworkManager.last_bind_error）；
  7. 运行中改配置明确提示"重启后生效"（P1-4）；
  8. 双击防火墙按钮导致双 UAC 的重复 connect 已删除（P1-4/双 UAC）。

历史修复清单（保持）：
  [1] 广播/单播模式下 IP 输入框均可编辑
  [2] 初始化时输入框默认启用
  [3] SignalTesterWidget 懒加载
  [4] _nm 为 None 时全面保护
  [5] 内嵌旧版 SignalTesterWidget 已移除
  [6] 输入/输出宇宙 SpinBox 最小值改为 1，无域0
  [7] 参数名适配 NetworkConfig 复数形式 input_universes / output_universes
  [8] 多宇宙支持：输入/输出宇宙可动态添加/删除多个域
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFrame, QTabWidget, QScrollArea, QHBoxLayout,
)
from PyQt6.QtCore import pyqtSignal, QTimer

from .signal_tester_widget import SignalTesterWidget
from .network_diagnostic_widget import NetworkDiagnosticWidget
# 保持旧导入路径兼容（控件已拆到 network_controls，此处再导出同名符号）。
from .network_controls import IPLineEdit, UniverseListPanel, UniverseRow  # noqa: F401
from .network_sections import (
    build_input_section, build_output_discovery_row,
    build_status_actions_section, build_firewall_hint,
)
from .network_status_card import NetworkStatusCard
from .network_status_helpers import (
    TAB_ICON_CONFIG, add_diagnostic_tab, add_signal_tester_tab,
)
from .firewall_allow import (
    FIREWALL_RULE_BASE, build_allow_command_text, run_elevated_allow,
    probe_allow_state,
)
from ..qascade_theme import TEXT_SECONDARY
from ..main_icon_library import load_tabler_icon
from ...core.models import NetworkConfig, Protocol

log = logging.getLogger(__name__)


def apply_project_network_config(win) -> None:
    """工程加载后把 ``project.network_config`` 灌进网络页（R182 P1-9）。

    修前 ``NetworkConfigPanel.load_config`` 全仓零调用——工程里保存的协议 /
    IP / 域配置从不回填界面，用户每次打开工程都要手填。

    同时给面板装好 **回写钩子**（界面 -> 工程，见
    :func:`store_project_network_config`）——这样 MainWindow 只需在工程加载
    路径调本函数一次，双向同步都成立（本页懒加载时回填会延迟到面板创建后，
    由面板从 NetworkManager 的当前配置自愈，见 ``_hydrate_from_manager``）。

    ``win`` 是 MainWindow（鸭子类型，不 import 它避免循环依赖）。任何异常
    只记日志：网络页回填失败不应阻断工程打开。调用方在 ``_load_project``
    里加一行 ``apply_project_network_config(self)`` 即可。
    """
    try:
        panel = getattr(win, "_network_widget", None)
        pm = getattr(win, "_pm", None)
        project = getattr(pm, "project", None) if pm is not None else None
        cfg = getattr(project, "network_config", None)
        if cfg is None:
            return
        if panel is not None:
            panel.load_config(cfg)   # 面板存在：直接回填界面
        # 无论面板是否已创建，都把工程配置灌进网络管理器：
        #   面板已存在 -> 界面与 NM 都已就绪；
        #   面板懒加载 -> NM 先拿到配置，面板创建时 _hydrate_from_manager 自愈。
        nm = getattr(win, "_nm", None)
        if nm is None and panel is not None:
            nm = getattr(panel, "_nm", None)
        if nm is not None:
            nm.configure(cfg)
            if panel is not None:
                panel.set_project_sink(
                    lambda new_cfg: store_project_network_config(win, new_cfg))
    except Exception as e:
        log.warning("工程网络配置回填失败: %s", e, exc_info=True)


def store_project_network_config(win, cfg) -> None:
    """把界面配置写回 ``project.network_config`` 并标脏（R182 P1-9）。

    与 :func:`apply_project_network_config` 成对：前者工程 -> 界面，本函数
    界面 -> 工程。落盘由既有保存流程负责。
    """
    try:
        pm = getattr(win, "_pm", None)
        project = getattr(pm, "project", None) if pm is not None else None
        if project is None or cfg is None:
            return
        project.network_config = cfg
        marker = getattr(pm, "_mark_dirty", None)
        if callable(marker):
            marker()
    except Exception as e:
        log.warning("工程网络配置回写失败: %s", e, exc_info=True)


# =============================================================================
#  NetworkConfigPanel
# =============================================================================

class NetworkConfigPanel(QWidget):
    config_changed = pyqtSignal(object)
    start_network  = pyqtSignal()
    stop_network   = pyqtSignal()

    def __init__(self, network_manager, parent=None):
        super().__init__(parent)
        self._nm = network_manager
        self._signal_tester_widget: SignalTesterWidget | None = None
        self._diagnostic_widget: NetworkDiagnosticWidget | None = None
        self._pending_allow_verify = False
        #: R182 审计修：运行中改配置置「待重启提示」标志。修前 500ms 定时刷新
        #: 把"需重启生效"提示直接覆盖成"运行中"，用户看不到警告。
        self._restart_pending = False
        #: 界面 -> 工程 回写钩子（由 apply_project_network_config 注入；
        #: 未注入时 _apply_config 退化为只写 NM，不影响功能）。
        self._project_sink = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)

        self.setWindowTitle("网络配置")
        self.setMinimumWidth(520)
        self._setup_ui()
        self._hydrate_from_manager()

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.addTab(
            self._build_config_tab(),
            load_tabler_icon(TAB_ICON_CONFIG, 14, TEXT_SECONDARY), "网络配置")

        add_diagnostic_tab(self._tabs)
        self._signal_tester_placeholder = add_signal_tester_tab(self._tabs)
        from .network_workspace_ui import install
        install(self, root)
    def _build_config_tab(self) -> QScrollArea:
        """配置页：滚动容器 + 三区；返回 QScrollArea 供 _setup_ui 入 Tab。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # ① 顶部状态卡
        self._status_card = NetworkStatusCard()
        lay.addWidget(self._status_card)

        # ①.5 深查③ N4/N5：输出绑定表 + 黑洞检测（逻辑全在新模块）
        try:
            from .network_binding_table import bind_output_audit_section
            self._binding_section = bind_output_audit_section(self)
            lay.addWidget(self._binding_section)
        except Exception:
            self._binding_section = None
            log.debug("绑定表区构建失败（不影响配置页）", exc_info=True)

        # ② 中部两区：左 = 输出，右 = 设备发现
        mid, w2, self._discovery_panel, _out_sec = build_output_discovery_row(self._nm)
        self._visualizer_preset_btn = w2["preset"]
        self._out_mode_combo = w2["out_mode"]
        self._out_ip = w2["out_ip"]
        self._out_universe_panel = w2["out_universes"]
        self._out_port_spin = w2["out_port"]
        self._sacn_priority_spin = w2["sacn_priority"]
        self._sacn_priority_row = w2["sacn_priority_row"]
        self._discovery_panel.fill_target_requested.connect(self._on_fill_target)
        lay.addWidget(mid)

        # 折叠：协议与输入监听
        conn, w = build_input_section()
        self._artnet_radio = w["artnet"]
        self._sacn_radio = w["sacn"]
        self._in_mode_combo = w["in_mode"]
        self._in_ip = w["in_ip"]
        self._in_universe_panel = w["in_universes"]
        self._in_enabled_check = w["in_enabled"]
        lay.addWidget(conn)

        # ③ 底部操作行 + 状态行
        status, self._status_label, self._network_empty_state, w3 = \
            build_status_actions_section()
        self._apply_btn = w3["apply"]
        self._start_btn = w3["start"]
        self._stop_btn = w3["stop"]
        self._fw_btn = w3["firewall"]
        self._status_section = status
        lay.addWidget(status)
        lay.addWidget(build_firewall_hint())
        lay.addStretch(1)

        self._connect_controls()
        scroll.setWidget(content)
        return scroll

    def _connect_controls(self):
        """信号接线（构建与业务分离；防火墙按钮单次连接——修前重复连接双 UAC）。"""
        self._artnet_radio.toggled.connect(self._on_protocol_toggled)
        self._in_mode_combo.currentIndexChanged.connect(self._on_in_mode_changed)
        self._out_mode_combo.currentIndexChanged.connect(self._on_out_mode_changed)
        self._in_enabled_check.toggled.connect(self._on_in_enabled_toggled)
        self._visualizer_preset_btn.clicked.connect(self._apply_visualizer_preset)
        self._apply_btn.clicked.connect(self._apply_config)
        self._start_btn.clicked.connect(self._start_network)
        self._stop_btn.clicked.connect(self._stop_network)
        # R182 P1-4 修：旧代码此处把同一槽连了两次 -> 点一次弹两次 UAC。
        self._fw_btn.clicked.connect(self._open_firewall_ports)

        # 初始态：sACN 行按协议隐藏；输入控件按未勾选灰掉；无效控件置灰。
        self._on_protocol_toggled(self._artnet_radio.isChecked())
        self._on_in_enabled_toggled(self._in_enabled_check.isChecked())

    # ── Tab 懒加载 ────────────────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        if index == 1 and self._diagnostic_widget is None:
            self._lazy_load_tab(1, self._make_diagnostic, "activity", "网络诊断")
        elif index == 2 and self._signal_tester_widget is None:
            self._lazy_load_tab(2, self._make_tester, "wave-sine", "信号测试")

    def _make_diagnostic(self) -> QWidget:
        self._diagnostic_widget = NetworkDiagnosticWidget(self)
        return self._diagnostic_widget

    def _make_tester(self) -> QWidget:
        self._signal_tester_widget = SignalTesterWidget(self._nm, self)
        return self._signal_tester_widget

    def _lazy_load_tab(self, index: int, factory, icon_name: str, title: str):
        """用真实控件替换占位 Tab（失败只记日志，保持占位不崩）。"""
        try:
            widget = factory()
        except Exception as e:
            log.error("%s 加载失败: %s", title, e, exc_info=True)
            return
        self._tabs.removeTab(index)
        self._tabs.insertTab(
            index, widget,
            load_tabler_icon(icon_name, 14, TEXT_SECONDARY), title)
        self._tabs.setCurrentIndex(index)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_protocol_toggled(self, _checked: bool = False):
        is_artnet = self._artnet_radio.isChecked()
        self._sacn_priority_row.setVisible(not is_artnet)
        self._out_port_spin.setValue(6454 if is_artnet else 5568)
        # P1-10：按协议决定哪几个控件真正生效（端口 / 发送模式互换）。
        self._apply_fixed_states()
        self._refresh_status()

    def _apply_fixed_states(self):
        """无效控件置灰 + 说明（P1-10）；输入监听勾选时输入 IP 仍可编辑。"""
        from .network_config_helpers import apply_control_states
        apply_control_states(self)

    def _on_in_mode_changed(self, idx: int):
        if idx == 0:
            if not self._in_ip.text().strip():
                self._in_ip.setText("0.0.0.0")
            self._in_ip.setPlaceholderText("0.0.0.0（监听全部网卡）")
        else:
            self._in_ip.setPlaceholderText("本机 IP 地址，例: 192.168.1.100")

    def _on_out_mode_changed(self, idx: int):
        from .network_config_helpers import update_output_ip_control
        update_output_ip_control(self)

    def _on_in_enabled_toggled(self, enabled: bool):
        # 未启用输入监听时，灰掉输入 IP，避免误以为在收数据（宇宙/模式已固定禁用）。
        try:
            self._in_ip.setEnabled(bool(enabled))
        except Exception:
            pass

    def _on_fill_target(self, ip: str):
        """发现表「填入目标地址」：写进输出目标 IP，并切到单播使目标生效。"""
        if not ip:
            return
        self._out_mode_combo.setCurrentIndex(1)
        self._out_ip.setText(ip)
        self.set_status("info", f"已把 {ip} 填入目标 IP（单播）；核对后点「应用配置」")

    def _apply_visualizer_preset(self):
        """一键把输出配成对接 可视化器：Art-Net / 端口 6454。

        默认广播 255.255.255.255：本机与同子网设备零配置直达（含跨设备）；
        用户已填单播 IP（跨网段定向）时保留不动。不自动 apply/启动——留给
        用户核对后点「应用配置」，避免误改正在运行的输出目标。
        """
        self._artnet_radio.setChecked(True)          # Art-Net
        self._out_port_spin.setValue(6454)           # 监听端口
        cur = self._out_ip.text().strip()
        if not cur or cur in ("255.255.255.255", "0.0.0.0"):
            self._out_mode_combo.setCurrentIndex(0)  # 广播：本机+同子网零配置
            self._out_ip.setText("255.255.255.255")
            self._out_ip.setPlaceholderText("255.255.255.255（全子网广播）")
            mode_txt = "广播 255.255.255.255（本机+同子网零配置）"
        else:
            self._out_mode_combo.setCurrentIndex(1)  # 单播：保护用户已填的 IP
            self._out_ip.setPlaceholderText("主机 IP（同机=127.0.0.1）")
            mode_txt = f"单播 {cur}（仅发送到此目标）"
        self.set_status(
            "info",
            f"预设已载入：Art-Net/6454 · {mode_txt}；跨网段可改单播填 IP")

    # ── 防火墙放行（P1-3：带引号 + 回探确认）──────────────────────────────

    def _open_firewall_ports(self):
        """提权放行本机 UDP 6454/5568 入站+出站，并回探确认是否真的生效。

        规则名与命令构造统一走 :mod:`.firewall_allow`（与后端诊断
        ``firewall_probe`` 同源、带引号——修前 ``name=雾白纪 DMX UDP``
        未加引号，netsh 在含空格规则名下必然失败）；ShellExecuteW 返回
        >32 只说明"进程起来了"，故提交后延迟回探一次，用实际规则状态说话。
        """
        try:
            run_elevated_allow()
        except Exception as e:
            self._show_firewall_failure(e)
            return
        self._pending_allow_verify = True
        self.set_status(
            "warn",
            "已提交防火墙放行请求，请在 UAC 弹窗中点「是」确认"
            "（本机 UDP 6454/5568 入站+出站）；随后自动回探确认")
        # netsh 生效有秒级延迟：延迟回探，用实际规则状态替换"已提交"话术。
        QTimer.singleShot(4000, self._verify_firewall_allow)

    def _verify_firewall_allow(self):
        """回探实际规则状态（P1-3：不看 ShellExecuteW 返回值下结论）。"""
        if not self._pending_allow_verify:
            return
        self._pending_allow_verify = False
        state = probe_allow_state()
        if state.all_allowed:
            self.set_status(
                "success",
                f"防火墙放行已生效（规则「{FIREWALL_RULE_BASE} In/Out」"
                "覆盖 UDP 6454/5568 入站+出站）")
        elif state.any_allowed:
            self.set_status(
                "warn",
                "防火墙放行部分生效："
                + ("；".join(state.details) or "请到诊断页查看逐项状态"))
        else:
            self.set_status(
                "error",
                "防火墙放行未生效（UAC 可能被拒绝，或系统策略阻止）。"
                "请用「防火墙放行」按钮 tooltip 里的管理员命令手动执行。")

    def _show_firewall_failure(self, err: Exception):
        """提权启动失败：红字 + 剪贴板命令（不弹模态框打断操作）。"""
        from PyQt6.QtWidgets import QApplication
        try:
            QApplication.clipboard().setText(build_allow_command_text())
        except Exception:
            log.debug("复制防火墙命令到剪贴板失败", exc_info=True)
        self.set_status(
            "error",
            f"防火墙放行启动失败（{err}）；管理员命令已复制到剪贴板，"
            "请在「管理员 CMD」中粘贴执行")

    # ── 状态行 ────────────────────────────────────────────────────────────

    def set_status(self, kind: str, text: str, *, hold_seconds: float = 8.0) -> None:
        """刷新状态行并**强制可见**（P1-8）。

        修前停机态把状态行 ``setVisible(False)``，而 fill / 预设 / 防火墙
        提示只 ``set_state`` -> 提示写进去了却看不见（用户实报"点了没反应"）。
        info/warn/error/success 一律可见；只有 dim（未启动空态）时才让位给
        空态引导。
        """
        from .network_config_helpers import set_panel_status
        set_panel_status(self, kind, text, hold_seconds)

    def _apply_config(self):
        """应用配置：运行中明确提示"停止并重启后生效"（P1-4）。"""
        from .network_config_helpers import apply_configuration
        apply_configuration(self)

    def _start_network(self):
        """启动网络：调 NetworkManager.start()，失败（端口占用）显示红字。"""
        from .network_config_helpers import start_network
        start_network(self)

    def _stop_network(self):
        """停止网络：直接调 NetworkManager.stop()，并保留信号供外部扩展。"""
        from .network_config_helpers import stop_network
        stop_network(self)

    def _refresh_status(self):
        """按**实际运行对象**刷新状态卡与状态行（P1-4：不再只读 _config）。"""
        from .network_config_helpers import refresh_panel_status
        refresh_panel_status(self)

    def _refresh_binding_section(self):
        """N4/N5 区节流刷新（≥2s；网卡枚举有 socket 调用，不跟 500ms 状态卡同频）。"""
        sec = getattr(self, "_binding_section", None)
        if sec is None:
            return
        now = time.monotonic()
        if now - getattr(self, "_binding_t", 0.0) < 2.0:
            return
        self._binding_t = now
        try:
            sec.refresh_now()
        except Exception:
            log.debug("绑定表刷新失败", exc_info=True)

    def _show_running_status(self) -> None:
        """运行中状态行（实际发送器详情；待重启提示合并显示）。"""
        from .network_config_helpers import show_running_status
        show_running_status(self)

    @staticmethod
    def _running_detail(st: dict, cfg, actual: str = "") -> str:
        """运行中详情：实际发送器/域数优先，回退配置摘要。"""
        parts = []
        target = actual or st.get("artnet_target")
        if target:
            parts.append(actual if actual else f"输出 -> {target}")
        if st.get("sacn_senders"):
            parts.append(f"{st['sacn_senders']} 个 sACN 发送器")
        if st.get("packets"):
            parts.append(f"已发 {int(st['packets']):,} 包")
        if st.get("errors"):
            parts.append(f"错误 {int(st['errors']):,}")
        if not parts and cfg is not None:
            in_unis = cfg.input_universes if cfg.input_universes else [1]
            out_unis = cfg.output_universes if cfg.output_universes else [1]
            parts.append(
                f"[{cfg.protocol.value}] 输入 {cfg.input_ip} "
                f"U{','.join(map(str, in_unis))} · 输出 {cfg.output_ip} "
                f"U{','.join(map(str, out_unis))}")
        return " · ".join(parts)

    # ── Public API ────────────────────────────────────────────────────────

    def set_project_sink(self, sink) -> None:
        """注入"界面 -> 工程"回写钩子（``callable(cfg)``；可传 None 解除）。"""
        self._project_sink = sink

    def _hydrate_from_manager(self) -> None:
        """面板创建后用 NM 当前配置自愈界面（P1-9 懒加载路径）。

        工程配置在 ``apply_project_network_config`` 时已灌进 NM，但网络页是
        懒加载的（懒创建时那次回填已过去）-> 这里从 NM 反读一次，保证"打开
        工程 -> 打开网络页"看到的就是工程里的值。
        """
        nm_cfg = getattr(self._nm, "_config", None) if self._nm is not None else None
        if nm_cfg is None:
            return
        try:
            self.load_config(nm_cfg)
        except Exception:
            log.debug("从 NetworkManager 反读网络配置失败", exc_info=True)

    def load_config(self, cfg: NetworkConfig):
        """从 NetworkConfig 对象刷新界面控件"""
        self._artnet_radio.setChecked(cfg.protocol == Protocol.ARTNET)
        self._sacn_radio.setChecked(cfg.protocol == Protocol.SACN)

        in_broadcast = cfg.input_mode == "broadcast"
        self._in_mode_combo.setCurrentIndex(0 if in_broadcast else 1)
        self._in_ip.setText(cfg.input_ip)
        self._in_universe_panel.set_values(
            cfg.input_universes if cfg.input_universes else [1])
        in_enabled = bool(getattr(cfg, "input_enabled", False))
        self._in_enabled_check.setChecked(in_enabled)
        self._on_in_enabled_toggled(in_enabled)

        out_broadcast = cfg.output_mode == "broadcast"
        self._out_mode_combo.setCurrentIndex(0 if out_broadcast else 1)
        self._out_ip.setText(cfg.output_ip)
        self._out_universe_panel.set_values(
            cfg.output_universes if cfg.output_universes else [1])
        self._out_port_spin.setValue(cfg.output_port)
        self._sacn_priority_spin.setValue(cfg.sacn_priority)
        self._apply_fixed_states()
        self._refresh_status()

    def get_config(self) -> NetworkConfig:
        """从界面控件读取当前配置"""
        proto = (Protocol.ARTNET if self._artnet_radio.isChecked()
                 else Protocol.SACN)
        return NetworkConfig(
            protocol=proto,
            input_mode=(
                "broadcast" if self._in_mode_combo.currentIndex() == 0
                else "unicast"),
            input_ip=self._in_ip.text().strip() or "0.0.0.0",
            input_universes=self._in_universe_panel.values(),
            input_enabled=self._in_enabled_check.isChecked(),
            output_mode=(
                "broadcast" if self._out_mode_combo.currentIndex() == 0
                else "unicast"),
            output_ip=self._out_ip.text().strip() or "255.255.255.255",
            output_universes=self._out_universe_panel.values(),
            output_port=self._out_port_spin.value(),
            sacn_priority=self._sacn_priority_spin.value(),
        )

    def stop_test(self):
        if self._signal_tester_widget is not None:
            self._signal_tester_widget.stop_test()

    def closeEvent(self, e):
        # R193/HF-01a：隐藏期间开销由 _refresh_status 的 isVisible 守卫消除；
        #   这里不停表（面板无 showEvent，停了重显后轮询无法自愈恢复）。
        self.stop_test()
        e.ignore()
        self.hide()

    def shutdown(self):
        """释放子面板资源（应用退出时调用；幂等）。"""
        self._timer.stop()
        try:
            self._discovery_panel.shutdown()
        except Exception:
            log.debug("ArtPoll 面板 shutdown 失败", exc_info=True)
