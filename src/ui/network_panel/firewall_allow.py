# -*- coding: utf-8 -*-
"""网络页「一键放行 DMX 端口」共享实现（R182 P1-3）。

修前问题（审计实锤）：
  1. ``network_diagnostic_widget.dmx_firewall_rules`` 构造的 netsh 参数把规则名
     写成 ``name=雾白纪 DMX UDP``——**未加引号**，规则名含空格 -> netsh 必败；
     而调用方只看 ``ShellExecuteW`` 返回值（只反映"进程是否起来"，不反映
     netsh 是否成功）-> 界面显示"已提交放行规则"= 假成功。
  2. 规则名两处不一致：配置页用 ``雾白纪 ULTRA DMX UDP In/Out``，诊断页用
     ``雾白纪 DMX UDP`` -> 诊断按自己的名字查（还查不到），永远误报。

本模块统一：
  - 规则名 :data:`FIREWALL_RULE_BASE` = ``雾白纪 ULTRA DMX``（与网络配置页
    tooltip 及后端 ``firewall_probe.RULE_NAME_PREFIXES[0]`` 同源）；
  - 命令构造带引号（cmd.exe 命令串路径必须带引号，与本机实测一致）；
  - 删除旧品牌规则（雾白纪 / 冬极白星 / Qascade）保证幂等且不残留；
  - 提权执行 :func:`run_elevated_allow`；
  - **回探确认** :func:`probe_allow_state`：放行后调后端探测真规则，用实际
    状态判定成功/部分成功/失败，不再拿 ShellExecuteW 的返回值当结论。

纯函数（:func:`build_allow_rules` / :func:`build_allow_command_text`）不触网、
不弹窗，可离线测试。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

#: 规则名前缀表与后端探测**同源**（后端按 "<前缀> DMX UDP <In|Out>" 展开查询）。
#: 这里是 P1-3 的根本修法：修前配置页写 ``雾白纪 ULTRA DMX UDP``、诊断页写
#: ``雾白纪 DMX UDP``，两套名字互不认账 -> 一键放行"成功"了诊断仍报失败。
from ...network.firewall_probe import RULE_NAME_PREFIXES, rule_display_names

log = logging.getLogger(__name__)

#: 新规则名前缀（最终规则 = ``<前缀> DMX UDP <In|Out>``）
FIREWALL_RULE_BASE = f"{RULE_NAME_PREFIXES[0]} DMX UDP"

#: 需要清理的历史品牌名前缀（先删后加，保证幂等可重复点击）
LEGACY_RULE_BASES: Tuple[str, ...] = tuple(
    f"{p} DMX UDP" for p in RULE_NAME_PREFIXES[1:])

#: 需要放行的 UDP 端口（Art-Net 6454 / sACN 5568）
DMX_PORTS = ("6454", "5568")

#: 回探等待（netsh 生效有秒级延迟；UI 侧用 QTimer 等这么久再查）
VERIFY_DELAY_MS = 4000


@dataclass
class AllowState:
    """放行回探结果（实际规则状态，而非"请求已提交"）。"""
    allowed_in: bool = False
    allowed_out: bool = False
    details: List[str] = field(default_factory=list)

    @property
    def all_allowed(self) -> bool:
        return self.allowed_in and self.allowed_out

    @property
    def any_allowed(self) -> bool:
        return self.allowed_in or self.allowed_out


def rule_name(direction: str) -> str:
    """方向 -> 完整规则名（``In`` / ``Out``，与后端探测表同名）。"""
    d = "Out" if str(direction).lower().startswith("out") else "In"
    return f"{FIREWALL_RULE_BASE} {d}"


def build_allow_rules() -> List[Tuple[str, ...]]:
    """返回两条放行规则的 netsh 参数（In/Out 各一条，各覆盖两个端口）。

    端口写在一个 ``localport=6454,5568`` 里（两种协议共用一条规则），
    与网络配置页 tooltip 的手动命令一致。
    """
    ports = ",".join(DMX_PORTS)
    return [
        (
            "advfirewall", "firewall", "add", "rule",
            f'name="{rule_name("in")}"',
            "dir=in", "action=allow", "protocol=UDP", f"localport={ports}",
        ),
        (
            "advfirewall", "firewall", "add", "rule",
            f'name="{rule_name("out")}"',
            "dir=out", "action=allow", "protocol=UDP", f"localport={ports}",
        ),
    ]


def build_delete_rules() -> List[Tuple[str, ...]]:
    """先删后加用的删除命令（新名 + 全部历史品牌名，各 In/Out 一条）。"""
    bases = (FIREWALL_RULE_BASE,) + LEGACY_RULE_BASES
    out: List[Tuple[str, ...]] = []
    for base in bases:
        for direction in ("In", "Out"):
            out.append((
                "advfirewall", "firewall", "delete", "rule",
                f'name="{base} {direction}"',
            ))
    return out


def build_allow_command_text() -> str:
    """完整 cmd.exe 命令串（先删旧名再添加；可直接粘贴到管理员 CMD 执行）。"""
    parts = ["netsh " + " ".join(rule) for rule in build_delete_rules()]
    parts += ["netsh " + " ".join(rule) for rule in build_allow_rules()]
    return " & ".join(parts)


def run_elevated_allow() -> None:
    """经 UAC 提权执行放行命令串（单次提权跑完全部规则）。

    失败（用户拒绝 UAC / ShellExecuteW 返回码 <32）抛 :class:`OSError`，
    由调用方决定降级提示。**返回值 >32 只表示"提权进程已启动"**——是否真的
    放行成功必须靠 :func:`probe_allow_state` 回探（P1-3 假成功的根因）。
    """
    import ctypes
    chain = build_allow_command_text()
    # SW_SHOWNORMAL=1；<32 为启动失败（含 UAC 被拒）
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "cmd.exe", f"/c {chain}", None, 1)
    if result < 32:
        raise OSError(f"ShellExecuteW 提权启动失败 (code={result})")


def probe_allow_state() -> AllowState:
    """回探本软件 DMX 规则的真实状态（调后端语言无关探测）。

    只认定"启用 + 覆盖 6454/5568 的入站/出站规则"为已放行；异常一律收敛
    为"未放行 + 明细"，不向 UI 抛。
    """
    from ...network.firewall_probe import probe_dmx_firewall_rules, rule_allows
    try:
        rules = probe_dmx_firewall_rules(rule_display_names())
    except Exception as e:
        log.warning("防火墙放行回探失败: %s", e, exc_info=True)
        return AllowState(details=[f"回探失败：{e}"])

    state = AllowState(
        allowed_in=rule_allows(rules, "in", "6454"),
        allowed_out=rule_allows(rules, "out", "6454"),
    )
    if state.allowed_in and state.allowed_out:
        state.details.append("入站/出站均已放行 UDP 6454/5568")
    if not state.allowed_in:
        state.details.append("入站规则缺失或被禁用")
    if not state.allowed_out:
        state.details.append("出站规则缺失或被禁用")
    if not rules:
        state.details.append("未找到本软件的防火墙规则（可能仍在等待 UAC 确认）")
    return state


def run_allow_and_verify(*, timeout_sec: float = 6.0) -> AllowState:
    """同步执行放行并回探（诊断页后台线程用；UI 线程勿直接调用）。

    先提权提交，再轮询回探直到通过或超时——供"一键放行后立即出结论"的
    场景使用；网络配置页走非阻塞的 QTimer 延迟回探路线。
    """
    import time
    try:
        run_elevated_allow()
    except Exception as e:
        log.info("提权放行未提交: %s", e)
        return AllowState(details=[f"提权未提交：{e}"])
    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    state = probe_allow_state()
    while not state.all_allowed and time.monotonic() < deadline:
        time.sleep(0.5)
        state = probe_allow_state()
    return state


def platform_supported() -> bool:
    """当前平台是否支持一键放行（仅 Windows 有 netsh advfirewall）。"""
    import sys
    return sys.platform == "win32"
