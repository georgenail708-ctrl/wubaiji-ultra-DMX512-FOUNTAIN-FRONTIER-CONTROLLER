# -*- coding: utf-8 -*-
"""R182 前端重做 — NetworkConfigPanel 的分区构建器（三区范式）。

页面结构（自上而下）：

  1. **顶部状态卡**（``network_status_card.NetworkStatusCard``）：协议 + 运行
     状态灯 + 绑定 IP + 输出目标摘要，一行大卡。
  2. **中部两区**（本模块两个 builder，横向并排）：
     左 = 输出（``build_output_section``：预设 / 目标 IP / 宇宙列表 /
     端口 / sACN 优先级）；右 = 设备发现（``build_discovery_section``：ArtPoll
     扫描 + 发现表 + 行内「填入」）。
  3. **底部操作行**（``build_status_actions_section``）：应用配置 / 启动 /
     停止 / 防火墙放行，统一按钮规格。

另含折叠的输入分区（``build_connection_section``：协议 + 输入监听，收进折叠区
避免抢占首屏——输入默认关闭，绝大多数用户不碰）与跨设备排查提示。

每个 builder 返回 ``(PanelSection, 控件 dict)``；信号连接与业务逻辑仍留在
NetworkConfigPanel（本模块只做呈现与版式，不接业务信号）。

按钮一律走 ``network_button_factory``（统一 22px/11px/14px 图标/令牌色）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel, QRadioButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from ..widget_kit import PanelSection, prop_row
from ..qascade_theme import STATUS_WARNING_INK as BRIGHT_WARN, TEXT_DISABLED, TEXT_SECONDARY
from .artpoll_panel import ArtPollDiscoveryPanel
from .network_button_factory import make_button
from .network_controls import IPLineEdit, UniverseListPanel
from .network_status_helpers import build_status_container

#: 分区头图标：复用 main_icon_library 的逻辑键（映射到 tabler 矢量）：
#: network -> network / audio_sim -> broadcast（发送语义）/ search / monitor /
#: help-circle。直接用 tabler 名会落 emoji 兜底，故必须走逻辑键。
_ICON_CONNECTION = "network"
_ICON_OUTPUT = "audio_sim"
_ICON_DISCOVERY = "search"
_ICON_STATUS = "monitor"
_ICON_HINT = "help-circle"

#: 未接后端的控件禁用说明（R182 P1-10：实为无效控件，禁用 + tooltip 讲清）
_FIXED_HINT = "当前版本固定，修改后不会生效（后端未消费该字段）"


def _disabled_tooltip(text: str) -> str:
    return f"{text}\n\n{_FIXED_HINT}"


def build_input_section() -> tuple:
    """输入分区（折叠）：协议选择 + 输入监听设置。返回 (分区, 控件字典)。

    默认折叠：输入接收器默认关闭（wildcard 收全网包会拖慢主线程），
    绝大多数用户不需要；折叠后首屏让给输出与设备发现两区。
    """
    sec = PanelSection("协议与输入 Connection & Input",
                       expanded=False, icon_key=_ICON_CONNECTION)
    form = QVBoxLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(4)

    artnet = QRadioButton("Art-Net")
    sacn = QRadioButton("sACN (E1.31)")
    artnet.setChecked(True)
    artnet.setToolTip("Art-Net（UDP 6454）：对接 / 常见控台与灯具")
    sacn.setToolTip("sACN E1.31（UDP 5568）：多播为主的演出协议")
    group = QButtonGroup(sec)
    group.addButton(artnet)
    group.addButton(sacn)
    proto_row = QHBoxLayout()
    proto_row.setContentsMargins(0, 0, 0, 0)
    proto_row.setSpacing(16)
    proto_row.addWidget(artnet)
    proto_row.addWidget(sacn)
    proto_row.addStretch()
    form.addLayout(proto_row)

    in_mode = QComboBox()
    in_mode.addItems(["通配接收（全部输入域）", "通配接收（全部输入域）"])
    in_mode.setEnabled(False)
    in_mode.setToolTip(_disabled_tooltip("接收模式"))
    form.addWidget(prop_row("接收方式（固定）", in_mode))

    in_ip = IPLineEdit("0.0.0.0（监听全部网卡）")
    in_ip.setText("0.0.0.0")
    form.addWidget(prop_row("绑定 IP", in_ip))

    in_universes = UniverseListPanel("输入宇宙（当前接收全部域）:", initial_values=[1])
    in_universes.setEnabled(False)
    in_universes.setToolTip(_disabled_tooltip("输入宇宙列表"))
    form.addWidget(in_universes)

    in_enabled = QCheckBox("启用输入监听（仅监视外部 DMX / 输入录制时需要）")
    in_enabled.setChecked(False)
    in_enabled.setToolTip(
        "默认关闭：本软件常态只向 /灯具输出。\n"
        "输入接收器在 wildcard 模式下会接收全网每一个 DMX 包并在主线程处理，\n"
        "无必要开启时会明显拖慢程序响应与输出。仅当确需监听外部信号时勾选。")
    form.addWidget(in_enabled)

    sec.add_layout(form)
    return sec, {
        "artnet": artnet, "sacn": sacn, "in_mode": in_mode, "in_ip": in_ip,
        "in_universes": in_universes, "in_enabled": in_enabled,
    }


# 旧名保留（既有调用/测试引用 build_connection_section）
build_connection_section = build_input_section


def build_output_section() -> tuple:
    """输出分区（中部左区）：预设 + 目标 IP / 宇宙 / 端口 / 优先级。"""
    sec = PanelSection("输出 Output", icon_key=_ICON_OUTPUT)
    form = QVBoxLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(4)

    preset = make_button("可视化器", "plug-connected", "primary",
                         tooltip=(
        "一键把输出配成 Art-Net / 端口 6454，对接 R4 可视化器。\n"
        "默认广播 255.255.255.255：本机与同子网设备零配置直达；\n"
        "若已填单播 IP（跨网段定向）则保留不动。\n"
        "跨网段（不同局域网）广播不可达：请用单播填 主机 IP。\n"
        "点击后核对目标 IP，再点「应用配置」+「启动网络」。"))
    preset_row = QHBoxLayout()
    preset_row.setContentsMargins(0, 0, 0, 0)
    preset_row.setSpacing(8)
    preset_row.addWidget(preset)
    preset_row.addStretch()
    form.addLayout(preset_row)

    out_mode = QComboBox()
    out_mode.addItems(["广播 (Broadcast)", "单播 (Unicast)"])
    out_mode.setToolTip(
        "广播：发向 255.255.255.255 与本机各网卡定向广播地址；\n"
        "单播：只发向目标 IP（跨网段必须用单播）。")
    form.addWidget(prop_row("发送模式", out_mode))

    out_ip = IPLineEdit("255.255.255.255（全子网广播）")
    out_ip.setText("255.255.255.255")
    out_ip.setToolTip("/ 目标主机 IP。同机 = 127.0.0.1；跨网段填对方 IP。")
    form.addWidget(prop_row("目标 IP（sACN 多播自动）", out_ip))

    out_universes = UniverseListPanel("输出宇宙（可多选）:", initial_values=[1])
    form.addWidget(out_universes)

    out_port = QSpinBox()
    out_port.setRange(1, 65535)
    out_port.setValue(6454)
    out_port.setFixedWidth(100)
    out_port.setToolTip("Art-Net 目标端口，标准 6454（监听此端口）")
    form.addWidget(prop_row("端口", out_port))

    sacn_priority = QSpinBox()
    sacn_priority.setRange(0, 200)
    sacn_priority.setValue(100)
    sacn_priority.setFixedWidth(100)
    sacn_priority.setToolTip("sACN 优先级（0-200，越大越优先；标准默认 100）")
    sacn_priority_row = prop_row("sACN 优先级", sacn_priority)
    form.addWidget(sacn_priority_row)

    sec.add_layout(form)
    return sec, {
        "preset": preset, "out_mode": out_mode, "out_ip": out_ip,
        "out_universes": out_universes, "out_port": out_port,
        "sacn_priority": sacn_priority, "sacn_priority_row": sacn_priority_row,
    }


def build_discovery_section(network_manager) -> tuple:
    """设备发现分区（中部右区，ArtPoll）。返回 (分区, ArtPollDiscoveryPanel)。"""
    sec = PanelSection("设备发现 Discovery (ArtPoll)", icon_key=_ICON_DISCOVERY)
    panel = ArtPollDiscoveryPanel(network_manager)
    sec.add_widget(panel)
    return sec, panel


def build_output_discovery_row(network_manager) -> tuple:
    """中部两区并排容器：左=输出，右=设备发现。

    返回 ``(container, output_controls, discovery_panel, output_section)``——
    分区对象单独回传，供调用方控制可见性（单栏窄屏时可只显示其一）。
    """
    from .network_workspace_ui import output_discovery
    out_sec, out_controls = build_output_section()
    disc_sec, disc_panel = build_discovery_section(network_manager)
    container = output_discovery(out_sec, disc_sec)
    return container, out_controls, disc_panel, out_sec


def build_status_actions_section() -> tuple:
    """底部操作行：状态行 + 空态 + 四个动作按钮（统一规格）。

    返回 ``(分区, 状态行, 空态, 控件字典)``。
    """
    sec = PanelSection("状态与操作 Status & Actions", icon_key=_ICON_STATUS,
                       expanded=True)
    container, status_line, empty_state = build_status_container()
    sec.add_widget(container)

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    apply_btn = make_button("应用配置", "check", "primary",
                            tooltip="把当前界面配置写入网络管理器（协议/IP/端口/宇宙）。\n"
                                    "运行中应用只会保存；需停止并重启后生效。")
    start_btn = make_button("启动网络", "player-play", "success",
                            tooltip="按已应用的配置启动 Art-Net / sACN 输出")
    stop_btn = make_button("停止网络", "player-stop", "danger",
                           tooltip="停止 DMX 输出（停止后需重新「启动网络」）")
    firewall_btn = make_button("防火墙放行", "shield-check", "neutral",
                               icon_color=BRIGHT_WARN,
                               tooltip="一键放行本机 UDP 6454/5568 入站+出站"
                                       "（Windows 防火墙默认阻止跨设备 UDP 入站）。")
    for btn in (apply_btn, start_btn, stop_btn, firewall_btn):
        row.addWidget(btn)
    row.addStretch()
    sec.add_layout(row)
    return sec, status_line, empty_state, {
        "apply": apply_btn, "start": start_btn, "stop": stop_btn,
        "firewall": firewall_btn,
    }


def build_firewall_hint() -> PanelSection:
    """跨设备排查提示（默认折叠，不占版面）。"""
    sec = PanelSection("跨设备排查提示（防火墙 / 广播 vs 单播）",
                       expanded=False, icon_key=_ICON_HINT)
    hint = QLabel(
        "提示：对接 时可选单播，填写接收电脑网卡的主机 IP；同机可用 127.0.0.1。"
        "广播/多播/单播由发送模式控制。更改后点「应用配置」；运行中需停止并重新启动。"
        "跨设备收不到信号时，确认接收端防火墙允许 UDP 6454/5568。下方「防火墙放行」处理本机规则。\n"
        "诊断页「防火墙状态」若显示失败，点「刷新诊断」重跑——"
        "本页放行采用固定规则名，诊断按同一规则名核对。")
    hint.setWordWrap(True)
    hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 2px 4px;")
    sec.add_widget(hint)

    manual = QLabel(
        "手动放行命令（管理员 CMD）：\n"
        '  netsh advfirewall firewall add rule name="雾白纪 ULTRA DMX UDP In" '
        "dir=in action=allow protocol=UDP localport=6454,5568\n"
        '  netsh advfirewall firewall add rule name="雾白纪 ULTRA DMX UDP Out" '
        "dir=out action=allow protocol=UDP localport=6454,5568")
    manual.setWordWrap(True)
    manual.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    manual.setStyleSheet(
        f"color:{TEXT_DISABLED};font-size:10px;"
        "font-family:'Consolas','DejaVu Sans Mono',monospace;padding:2px 4px;")
    sec.add_widget(manual)
    return sec


def apply_fixed_control_states(controls: dict, *, artnet: bool) -> None:
    """把"后端不消费"的控件按当前协议统一置灰 + tooltip 说明（R182 P1-10）。

    审计结论（后端零消费字段）：
      - 接收模式 / 输入宇宙列表：``NetworkConfig`` 里往返持久化，但
        NetworkManager 的接收路径只读 ``input_ip`` 与 ``input_enabled``
        （wildcard 收全部包）-> 界面改了不生效；
      - sACN 端口：``SACNSender`` 固定发到 ``SACN_PORT``(5568)，
        ``output_port`` 只被 Art-Net 发送器消费 -> sACN 下该框无效；
      - Art-Net 与 sACN 发送模式均被发送链消费，可编辑。

    置灰而非删除：字段仍在工程文件里往返持久化，删掉会让老工程配置悄悄漂移。
    """
    always_fixed = {
        "in_mode": "接收模式（接收路径固定为通配接收，不看此值）",
        "in_universes": "输入宇宙列表（接收器通配收全部域，不看此列表）",
    }
    for key, label in always_fixed.items():
        _grey_out(controls.get(key), label)

    mode = controls.get("out_mode")
    _enable(mode)
    if mode is not None:
        mode.setItemText(0, "广播 (Broadcast)" if artnet else "多播 (Multicast)")
        mode.setToolTip("单播：只发送到目标主机 IP。" + (
            "广播：发送到配置地址及本机网卡广播地址。" if artnet else
            "多播：按输出宇宙自动生成 239.255.x.x 目标。"))
    if artnet:
        _enable(controls.get("out_port"))
        controls["out_port"].setToolTip("Art-Net 目标端口；标准 6454，可按接收端修改。")
    else:
        port = controls.get("out_port")
        if port is not None:
            port.setEnabled(False)
            port.setValue(5568)
            port.setToolTip("sACN 使用协议规定的 UDP 5568；发送模式与目标 IP 可按需配置。")


def _grey_out(widget, label: str) -> None:
    """禁用 + 说明 tooltip（None 安全）。"""
    if widget is None:
        return
    try:
        widget.setEnabled(False)
        widget.setToolTip(_disabled_tooltip(label))
    except Exception:
        pass


def _enable(widget) -> None:
    if widget is None:
        return
    try:
        widget.setEnabled(True)
    except Exception:
        pass


__all__ = [
    "build_input_section", "build_connection_section", "build_output_section",
    "build_discovery_section", "build_output_discovery_row",
    "build_status_actions_section", "build_firewall_hint",
    "apply_fixed_control_states",
]
